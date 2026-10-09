import copy
import io
import json
import unittest
from tool_search_adapter import BRIDGE_NAME, ToolSearchAdapter


SEARCH = {"type": "tool_search", "execution": "client", "description": "fixture",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}


def namespace(*names):
    return {"type": "namespace", "name": "mcp__fixture", "tools": [
        {"type": "function", "name": n, "defer_loading": True,
         "parameters": {"type": "object", "properties": {"defer_loading": {"type": "boolean"}}}}
        for n in names]}


def search_history(call_id="c1", names=("read",)):
    return [{"type": "tool_search_call", "call_id": call_id, "execution": "client",
             "arguments": {"query": "read fixture"}},
            {"type": "tool_search_output", "call_id": call_id, "execution": "client",
             "tools": [namespace(*names)]}]


class FragmentReader:
    def __init__(self, data, size):
        self.source = io.BytesIO(data)
        self.size = size

    def read1(self, size):
        return self.source.read(min(self.size, size))


class AdapterTests(unittest.TestCase):
    def test_only_company_routes_are_eligible(self):
        payload = {"tools": [SEARCH], "input": search_history()}
        for route in ("deepseek", "glm"):
            result, adapter = ToolSearchAdapter.prepare(payload, route)
            self.assertIsNotNone(adapter)
            self.assertEqual(result["tools"][0]["name"], BRIDGE_NAME)
        result, adapter = ToolSearchAdapter.prepare(payload, "openai")
        self.assertIs(result, payload)
        self.assertIsNone(adapter)

    def test_no_native_search_is_noop(self):
        payload = {"tools": [namespace("read")], "input": []}
        result, adapter = ToolSearchAdapter.prepare(payload, "deepseek")
        self.assertIs(result, payload)
        self.assertIsNone(adapter)

    def test_discovered_tools_merged_and_call_linkage_preserved(self):
        payload = {"tools": [SEARCH, namespace("read")],
                   "input": search_history(names=("read", "write")) + search_history("c2", ("other",)) + [
                       {"type": "function_call", "namespace": "mcp__fixture", "name": "read", "call_id": "real", "arguments": "{}"},
                       {"type": "function_call_output", "call_id": "real", "output": "secret"}]}
        before = copy.deepcopy(payload)
        result, _ = ToolSearchAdapter.prepare(payload, "glm")
        self.assertEqual(payload, before)
        self.assertEqual(result["input"][0]["call_id"], result["input"][1]["call_id"])
        self.assertEqual(result["input"][-2:], payload["input"][-2:])
        self.assertEqual(json.loads(result["input"][0]["arguments"]), {"query": "read fixture"})
        children = result["tools"][1]["tools"]
        self.assertEqual([c["name"] for c in children], ["read", "write", "other"])
        self.assertTrue(all("defer_loading" not in c for c in children))
        self.assertIn("defer_loading", children[0]["parameters"]["properties"])

    def test_compaction_history_without_declaration(self):
        result, _ = ToolSearchAdapter.prepare({"input": search_history()}, "deepseek")
        self.assertEqual(result["input"][0]["type"], "function_call")
        self.assertEqual(result["tools"][0]["name"], "mcp__fixture")

    def test_collision_and_bad_history_rejected(self):
        for bad in [{"tools": [SEARCH, {"type": "function", "name": BRIDGE_NAME}]},
                    {"tools": [dict(SEARCH, execution="server")]},
                    {"input": [{"type": "tool_search_call", "arguments": {}}]},
                    {"input": [dict(search_history()[0], arguments="not-json")]},
                    {"tools": [SEARCH, SEARCH]}]:
            with self.subTest(payload=bad), self.assertRaises(ValueError):
                ToolSearchAdapter.prepare(bad, "glm")

    def test_forced_search_and_empty_results(self):
        payload = {"tools": [SEARCH], "tool_choice": {"type": "tool_search"},
                   "input": [search_history()[0], dict(search_history()[1], tools=[])]}
        result, _ = ToolSearchAdapter.prepare(payload, "deepseek")
        self.assertEqual(result["tool_choice"], {"type": "function", "name": BRIDGE_NAME})
        self.assertEqual(json.loads(result["input"][1]["output"]), {"tools": []})

    def test_stream_fragmentation_parallel_calls_and_usage(self):
        calls = [{"type": "function_call", "name": name, "id": "i" + str(i), "call_id": "c" + str(i),
                  "arguments": '{"query":"中文"}', "status": "completed"}
                 for i, name in enumerate([BRIDGE_NAME, "ordinary", BRIDGE_NAME])]
        events = []
        for i, item in enumerate(calls):
            events.append({"type": "response.output_item.added", "output_index": i,
                           "item": dict(item, arguments="", status="in_progress")})
        for i, item in enumerate(calls):
            events.extend([{"type": "response.function_call_arguments.delta", "output_index": i, "item_id": item["id"], "delta": item["arguments"]},
                           {"type": "response.function_call_arguments.done", "output_index": i, "item_id": item["id"], "arguments": item["arguments"]},
                           {"type": "response.output_item.done", "output_index": i, "item": item}])
        response = {"output": calls, "usage": {"input_tokens": 123}, "status": "completed"}
        events.append({"type": "response.completed", "response": response})
        for delimiter in (b"\n", b"\r\n"):
            data = (delimiter * 2).join(b"event: " + e["type"].encode() + delimiter + b"data: " + json.dumps(e, ensure_ascii=False).encode() for e in events) + delimiter * 2
            for size in (1, 7, 65536):
                adapter = ToolSearchAdapter()
                raw = b"".join(adapter.stream(FragmentReader(data, size)))
                converted = [json.loads(l[6:]) for l in raw.splitlines() if l.startswith(b"data: ")]
                deltas = [e for e in converted if e["type"].startswith("response.function_call_arguments.")]
                self.assertEqual(len(deltas), 2)
                self.assertTrue(all(e["item_id"] == "i1" for e in deltas))
                final = converted[-1]["response"]
                self.assertEqual(final["usage"], response["usage"])
                self.assertEqual([i["type"] for i in final["output"]], ["tool_search_call", "function_call", "tool_search_call"])
                self.assertEqual(final["output"][0]["arguments"], {"query": "中文"})
                self.assertEqual(final["output"][1], calls[1])

    def test_normal_events_pass_through_and_invalid_arguments_fail(self):
        a = ToolSearchAdapter()
        frame = b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"abc"}'
        self.assertEqual(a.frame(frame), frame + b"\n\n")
        self.assertEqual(a.frame(b"data: [DONE]"), b"data: [DONE]\n\n")
        with self.assertRaises(ValueError):
            a.output_item({"type": "function_call", "name": BRIDGE_NAME, "arguments": "broken"})


if __name__ == "__main__":
    unittest.main()
