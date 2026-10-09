"""Loopback-only model router. Never logs prompts, responses, or credentials."""
import argparse
import hmac
import importlib.util
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The native model bridge loads this file by absolute path, outside sys.path.
_spec = importlib.util.spec_from_file_location("local_tool_search_adapter", Path(__file__).with_name("tool_search_adapter.py"))
_adapter_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter_module)
ToolSearchAdapter = _adapter_module.ToolSearchAdapter

OPENAI_BASE = "https://chatgpt.com/backend-api/codex"
GLM_BASE = "https://glm.example.invalid/v1"
GLM_MODEL = "glm-5.3-flash"
DEEPSEEK_BASE = "https://deepseek.example.invalid/v1"
DEEPSEEK_MODEL = "deepseek-v4-1-flash-260910"
COMPANY_ROUTES = {
    GLM_MODEL: (GLM_BASE, "glm"),
    DEEPSEEK_MODEL: (DEEPSEEK_BASE, "deepseek"),
}
ALLOWED_PATHS = {"/responses", "/responses/compact"}
HOP_HEADERS = {"host", "connection", "proxy-connection", "keep-alive", "transfer-encoding",
               "upgrade", "te", "trailer", "proxy-authorization", "proxy-authenticate",
               "content-length", "x-codex-router-token"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_settings(path):
    settings = json.loads(Path(path).read_text())
    if not isinstance(settings, dict) or set(settings) - {
            "routes", "tool_search", "context_window", "effective_context_window_percent"}:
        raise ValueError("Unsupported router settings")
    if set(settings.get("routes", {})) != set(COMPANY_ROUTES):
        raise ValueError("Configure both exact GLM and DeepSeek model IDs")
    if not isinstance(settings.get("tool_search", True), bool):
        raise ValueError("tool_search must be a boolean")
    for route in settings["routes"].values():
        if not isinstance(route, dict) or set(route) - {"base_url", "api_key_env"}:
            raise ValueError("Routes accept base_url and optional api_key_env only")
        url = urllib.parse.urlsplit(route.get("base_url", ""))
        if (url.scheme not in ("http", "https") or not url.hostname or
                url.hostname.endswith(".invalid") or url.username or url.password or
                url.query or url.fragment):
            raise ValueError("Set a real upstream base_url without credentials/query/fragment")
        env = route.get("api_key_env")
        if env and (not isinstance(env, str) or not env.replace("_", "a").isalnum()):
            raise ValueError("api_key_env must name an environment variable")
    for key, default, maximum in (("context_window", 1048576, 10000000),
                                 ("effective_context_window_percent", 95, 100)):
        value = settings.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("Invalid " + key)
    return settings


def select_route(model, incoming_headers, settings=None):
    if model in COMPANY_ROUTES:
        # Deliberate allowlist: no OpenAI auth, account identifiers, cookies or session headers.
        base, route = COMPANY_ROUTES[model]
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "Accept-Encoding": "identity"}
        if settings is not None:
            configured = settings["routes"][model]
            base = configured["base_url"].rstrip("/")
            env = configured.get("api_key_env")
            if env:
                key = os.environ.get(env)
                if not key:
                    raise ValueError("Required upstream API key environment variable is missing")
                headers["Authorization"] = "Bearer " + key
        return base, headers, route
    if isinstance(model, str) and model.startswith("gpt-"):
        headers = {k: v for k, v in incoming_headers.items()
                   if k.lower() not in HOP_HEADERS and k.lower() != "accept-encoding"}
        headers["Accept-Encoding"] = "identity"
        return OPENAI_BASE, headers, "openai"
    raise ValueError("Model is not configured in the local router")


def normalize_history(payload, route):
    """Provider-owned reasoning IDs are not portable; retain ordinary conversation/tool data."""
    result = dict(payload)
    history = result.get("input")
    if not isinstance(history, list):
        return result
    normalized = []
    for original in history:
        if not isinstance(original, dict):
            normalized.append(original)
            continue
        item = dict(original)
        if item.get("type") == "reasoning":
            # OpenAI can recover its stateless reasoning from encrypted content.
            # Company-model reasoning IDs cannot be looked up on OpenAI, and
            # company gateways cannot consume OpenAI encrypted reasoning.
            if route != "openai" or not item.get("encrypted_content"):
                continue
        # Inline messages and tool results are complete without provider item IDs.
        # call_id must remain intact so each tool result matches its call.
        if item.get("type") != "item_reference":
            item.pop("id", None)
        normalized.append(item)
    result["input"] = normalized
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok", "service": "codex-model-router"})
        elif self.path == "/status":
            if not hmac.compare_digest(self.headers.get("X-Codex-Router-Token", ""), self.server.router_secret):
                self.reply(403, {"error": "Local router authentication failed"})
                return
            with self.server.activity_lock:
                self.reply(200, {"active_requests": self.server.active_requests})
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        expected = self.server.router_secret
        supplied = self.headers.get("X-Codex-Router-Token", "")
        if not hmac.compare_digest(supplied, expected):
            self.close_connection = True
            self.reply(403, {"error": "Local router authentication failed"})
            return
        path = self.path[3:] if self.path.startswith("/v1/") else self.path
        if path not in ALLOWED_PATHS:
            self.close_connection = True
            self.reply(404, {"error": "Unsupported endpoint"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 64 * 1024 * 1024:
                raise ValueError("Unsupported request size")
            if self.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("Unsupported request compression")
            body = self.rfile.read(size)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object")
            model = payload.get("model")
            if not isinstance(model, str):
                raise ValueError("model must be a string")
            base, headers, route = select_route(model, self.headers, self.server.settings)
            outgoing = normalize_history(payload, route)
            if self.server.settings.get("tool_search", True):
                outgoing, adapter = ToolSearchAdapter.prepare(outgoing, route)
            else:
                adapter = None
            body = json.dumps(outgoing, ensure_ascii=False).encode()
        except (ValueError, TypeError) as exc:
            self.close_connection = True
            self.reply(400, {"error": str(exc)})
            return
        started = time.monotonic()
        status = 502
        response_started = False
        transport_complete = False
        with self.server.activity_lock:
            self.server.active_requests += 1
        self.server.event({"event": "request", "model": model, "route": route, "path": path,
                           "reasoning_effort": (payload.get("reasoning") or {}).get("effort")
                           if isinstance(payload.get("reasoning"), dict) else None,
                           "auth_forwarded": any(k.lower() == "authorization" for k in headers)})
        try:
            req = urllib.request.Request(base + path, data=body, headers=headers, method="POST")
            try:
                upstream = self.server.opener.open(req, timeout=300)
            except urllib.error.HTTPError as exc:
                upstream = exc
            with upstream:
                status = upstream.code
                self.send_response(status)
                for k, v in upstream.headers.items():
                    if k.lower() not in HOP_HEADERS:
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                response_started = True
                self.close_connection = True
                if adapter and status == 200 and "text/event-stream" in upstream.headers.get("Content-Type", ""):
                    chunks = adapter.stream(upstream)
                elif adapter and status == 200:
                    chunks = [json.dumps(adapter.response(json.loads(upstream.read())), ensure_ascii=False).encode()]
                else:
                    chunks = iter(lambda: upstream.read1(65536), b"")
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                transport_complete = True
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.server.event({"event": "upstream_error", "route": route, "kind": type(exc).__name__})
            if not response_started:
                self.reply(502, {"error": "Upstream connection failed"})
        finally:
            with self.server.activity_lock:
                self.server.active_requests -= 1
            self.server.event({"event": "finished", "model": model, "route": route,
                               "status": status, "transport_complete": transport_complete,
                               "seconds": round(time.monotonic() - started, 3)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=17864)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--secret-file", required=True)
    parser.add_argument("--log-file", required=True)
    args = parser.parse_args()
    settings = load_settings(args.settings)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.router_secret = Path(args.secret_file).read_text().strip()
    if len(server.router_secret) < 24:
        server.server_close()
        raise ValueError("Router secret must contain at least 24 characters")
    server.settings = settings
    server.active_requests = 0
    server.activity_lock = threading.Lock()
    lock = threading.Lock()
    def event(obj):
        obj["time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with lock:
            log = Path(args.log_file)
            if log.exists() and log.stat().st_size > 4 * 1024 * 1024:
                log.replace(log.with_suffix(".previous.jsonl"))
            with log.open("a") as f:
                f.write(json.dumps(obj) + "\n")
    server.event = event
    server.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    event({"event": "started", "port": args.port})
    server.serve_forever()


if __name__ == "__main__":
    main()
