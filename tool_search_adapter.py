"""Stateless Responses client tool-search bridge. Never searches or executes tools.

Only the selected tool definitions returned by Codex are made visible upstream.
Per-request state tracks streaming items; no conversation or credential is stored.
"""
import copy
import json

BRIDGE_NAME = "codex_local_tool_search"
MAX_FRAME_BYTES = 16 * 1024 * 1024


def clean_tool(tool):
    result = copy.deepcopy(tool)
    result.pop("defer_loading", None)
    if result.get("type") == "namespace":
        result["tools"] = [clean_tool(t) for t in result.get("tools", [])]
    return result


def merge_tools(declared, discovered):
    """Deduplicate by namespace/name, with current declarations authoritative."""
    result = []
    positions = {}
    for tool in declared + discovered:
        tool = clean_tool(tool)
        key = (tool.get("type"), tool.get("namespace"), tool.get("name"))
        if key not in positions:
            positions[key] = len(result)
            result.append(tool)
        elif tool.get("type") == "namespace":
            current = result[positions[key]]
            current["tools"] = merge_tools(current.get("tools", []), tool.get("tools", []))
    return result


def is_bridge(item):
    return (item.get("type") == "function_call" and
            item.get("name") == BRIDGE_NAME and not item.get("namespace"))


def search_arguments(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or not isinstance(value.get("query"), str):
        raise ValueError("Invalid tool-search arguments")
    return value


class ToolSearchAdapter:
    def __init__(self):
        self.item_ids = set()
        self.output_indices = set()

    @classmethod
    def prepare(cls, payload, route):
        if route not in ("deepseek", "glm"):
            return payload, None
        declarations = payload.get("tools", [])
        history = payload.get("input", [])
        search = [t for t in declarations if t.get("type") == "tool_search"]
        native_history = isinstance(history, list) and any(
            isinstance(i, dict) and i.get("type") in ("tool_search_call", "tool_search_output")
            for i in history)
        if not search and not native_history:
            return payload, None
        if len(search) > 1 or any(t.get("execution", "client") != "client" for t in search):
            raise ValueError("Only one client tool-search declaration is supported")
        for t in declarations:
            if t.get("name") == BRIDGE_NAME:
                raise ValueError("Tool-search bridge name collision")
        result = copy.deepcopy(payload)
        declared = []
        for t in declarations:
            if t.get("type") == "tool_search":
                declared.append({"type": "function", "name": BRIDGE_NAME,
                                 "description": "Search for deferred tools available to this session. "
                                 "Call this function to discover tools before calling them.\n" + t.get("description", ""),
                                 "parameters": t["parameters"]})
            else:
                declared.append(t)
        discovered = []
        if isinstance(history, list):
            items = []
            for original in history:
                if not isinstance(original, dict):
                    items.append(original)
                    continue
                item = copy.deepcopy(original)
                kind = item.get("type")
                if kind in ("tool_search_call", "tool_search_output"):
                    if item.get("execution", "client") != "client":
                        raise ValueError("Server tool-search history is unsupported")
                    if not item.get("call_id"):
                        raise ValueError("Missing tool-search call_id")
                if kind == "tool_search_call":
                    item = {"type": "function_call", "call_id": item["call_id"],
                            "name": BRIDGE_NAME,
                            "arguments": json.dumps(search_arguments(item["arguments"]), ensure_ascii=False)}
                elif kind == "tool_search_output":
                    found = item.get("tools", [])
                    if not isinstance(found, list) or any(not isinstance(t, dict) for t in found):
                        raise ValueError("Invalid tool-search output tools")
                    if any(t.get("name") == BRIDGE_NAME for t in found):
                        raise ValueError("Discovered tool collides with bridge name")
                    discovered.extend(found)
                    item = {"type": "function_call_output", "call_id": item["call_id"],
                            "output": json.dumps({"tools": [clean_tool(t) for t in found]}, ensure_ascii=False)}
                items.append(item)
            result["input"] = items
        if declarations or discovered:
            result["tools"] = merge_tools(declared, discovered)
        choice = result.get("tool_choice")
        if isinstance(choice, dict) and choice.get("type") == "tool_search":
            result["tool_choice"] = {"type": "function", "name": BRIDGE_NAME}
        return result, cls()

    def output_item(self, item, added=False):
        if not is_bridge(item):
            return item
        result = {k: item[k] for k in ("id", "call_id", "status") if k in item}
        result.update(type="tool_search_call", execution="client",
                      arguments={} if added else search_arguments(item.get("arguments")))
        return result

    def response(self, response):
        result = dict(response)
        if isinstance(result.get("output"), list):
            result["output"] = [self.output_item(i) for i in result["output"]]
        return result

    def event(self, event):
        kind = event.get("type", "")
        item = event.get("item", {})
        if kind.startswith("response.output_item.") and is_bridge(item):
            if item.get("id"):
                self.item_ids.add(item["id"])
            if "output_index" in event:
                self.output_indices.add(event["output_index"])
            result = dict(event)
            result["item"] = self.output_item(item, added=kind.endswith(".added"))
            return result
        if kind.startswith("response.function_call_arguments.") and (
                event.get("item_id") in self.item_ids or event.get("output_index") in self.output_indices):
            # Native search arguments are an object delivered with output_item.done.
            return None
        if isinstance(event.get("response"), dict):
            result = dict(event)
            result["response"] = self.response(event["response"])
            return result
        return event

    def frame(self, frame):
        lines = frame.splitlines()
        data = b"\n".join(line[5:].lstrip(b" ") for line in lines if line.startswith(b"data:"))
        if not data or data == b"[DONE]":
            return frame + b"\n\n"
        event = json.loads(data)
        converted = self.event(event)
        if converted is None:
            return b""
        if converted == event:
            return frame + b"\n\n"
        metadata = [line for line in lines if not line.startswith((b"data:", b"event:"))]
        metadata.extend([b"event: " + converted["type"].encode(),
                         b"data: " + json.dumps(converted, ensure_ascii=False).encode()])
        return b"\n".join(metadata) + b"\n\n"

    def stream(self, upstream):
        """Bound memory by SSE frame, preserving normal token streaming."""
        pending = b""
        while True:
            chunk = upstream.read1(65536)
            if not chunk:
                break
            pending += chunk
            while True:
                lf, crlf = pending.find(b"\n\n"), pending.find(b"\r\n\r\n")
                offsets = [(i, n) for i, n in ((lf, 2), (crlf, 4)) if i >= 0]
                if not offsets:
                    break
                end, width = min(offsets)
                if end > MAX_FRAME_BYTES:
                    raise ValueError("SSE frame exceeds adapter limit")
                frame, pending = pending[:end], pending[end + width:]
                yield self.frame(frame)
            if len(pending) > MAX_FRAME_BYTES:
                raise ValueError("SSE frame exceeds adapter limit")
        if pending.strip():
            yield self.frame(pending)
