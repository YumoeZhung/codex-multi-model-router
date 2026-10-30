"""Loopback-only model router. Never logs prompts, responses, or credentials."""
import argparse
import hmac
import importlib.util
import json
import os
import ssl
import subprocess
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
GLM_MODEL = "glm-5-3-flash-260828"
DEEPSEEK_BASE = "https://deepseek.example.invalid/v1"
DEEPSEEK_MODEL = "deepseek-v4-1-flash-261017"
COMPANY_ROUTES = {
    GLM_MODEL: (GLM_BASE, "glm"),
    DEEPSEEK_MODEL: (DEEPSEEK_BASE, "deepseek"),
}
ALLOWED_PATHS = {"/responses", "/responses/compact"}
HOP_HEADERS = {"host", "connection", "proxy-connection", "keep-alive", "transfer-encoding",
               "upgrade", "te", "trailer", "proxy-authorization", "proxy-authenticate",
               "content-length", "x-codex-router-token"}
CATALOG_LOCK = threading.Lock()
CATALOG_STATE = {"binary": None, "identity": None, "mtime": 0, "error": None, "refresh_count": 0}


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


def build_catalog(bundled, additions):
    """Merge current GPT entries with exact local provider additions."""
    models = bundled.get("models") if isinstance(bundled, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("Bundled Codex catalog is empty or malformed")
    output = []
    seen = set()
    for original in models:
        if not isinstance(original, dict) or not isinstance(original.get("slug"), str):
            raise ValueError("Bundled Codex catalog has an invalid model")
        slug = original["slug"]
        if not slug or slug in seen:
            raise ValueError("Bundled Codex catalog has an invalid or duplicate slug")
        seen.add(slug)
        model = json.loads(json.dumps(original))
        if slug.startswith("gpt-"):
            model["visibility"] = "list"
        output.append(model)
    for slug in COMPANY_ROUTES:
        if slug in seen:
            raise ValueError("Local provider model conflicts with bundled Codex catalog")
        model = json.loads(json.dumps(additions[slug]))
        model["visibility"] = "list"
        output.append(model)
        seen.add(slug)
    return {"models": output}


def catalog_payload(server):
    """Read the installed Codex catalog, falling back to the last good cache."""
    binary = Path(server.catalog_binary).expanduser()
    cache = Path(server.catalog_cache)
    try:
        stat = binary.stat()
        identity = (str(binary.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError as exc:
        return read_cached_catalog(cache, exc)
    with CATALOG_LOCK:
        state = CATALOG_STATE
        identity_changed = state["binary"] != identity[0] or state["identity"] != identity[1]
        if identity_changed:
            state["error"] = None
        if state["binary"] == identity[0] and state["identity"] == identity[1] and state["error"] is None:
            if state["mtime"] and time.time() - state["mtime"] < 300:
                try:
                    return json.loads(cache.read_text())
                except (OSError, ValueError):
                    pass
        try:
            result = subprocess.run(
                [str(binary), "debug", "models", "--bundled"],
                capture_output=True, text=True, timeout=30,
                env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin",
                     "NO_PROXY": "127.0.0.1,localhost"},
            )
            if result.returncode:
                raise RuntimeError("Installed Codex model command failed")
            catalog = build_catalog(json.loads(result.stdout), server.catalog_additions)
            data = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()
            tmp = cache.with_name(f".{cache.name}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            tmp.chmod(0o600)
            tmp.replace(cache)
            state.update(binary=identity[0], identity=identity[1], mtime=time.time(), error=None)
            state["refresh_count"] += 1
            return catalog
        except Exception as exc:
            state["error"] = type(exc).__name__
            return read_cached_catalog(cache, exc)


def read_cached_catalog(cache, cause):
    try:
        catalog = json.loads(cache.read_text())
        if not isinstance(catalog.get("models"), list) or not catalog["models"]:
            raise ValueError("empty catalog")
        return catalog
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError("Codex catalog refresh failed and no cache exists") from exc


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
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self.reply(200, {"status": "ok", "service": "codex-model-router"})
        elif path in ("/v1/models", "/models"):
            if not hmac.compare_digest(self.headers.get("X-Codex-Router-Token", ""), self.server.router_secret):
                self.reply(403, {"error": "Local router authentication failed"})
                return
            try:
                catalog = catalog_payload(self.server)
            except ValueError as exc:
                self.reply(503, {"error": str(exc)})
                return
            self.reply(200, catalog)
        elif path == "/status":
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
    parser.add_argument("--catalog-binary", required=True)
    parser.add_argument("--catalog-cache", required=True)
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
    server.catalog_binary = args.catalog_binary
    server.catalog_cache = args.catalog_cache
    additions = {}
    for slug in COMPANY_ROUTES:
        path = Path(args.settings).with_name("model-templates") / (slug + ".json")
        additions[slug] = json.loads(path.read_text())
    server.catalog_additions = additions
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
