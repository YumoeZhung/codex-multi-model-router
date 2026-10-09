import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import router


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.received.append((self.path, dict(self.headers), payload))
        self.send_response(200)
        if payload.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            item = {"type": "function_call", "id": "f1", "call_id": "c1",
                    "name": "codex_local_tool_search", "arguments": '{"query":"fixture"}'}
            for event in [
                {"type": "response.output_item.added", "output_index": 0, "item": dict(item, arguments="")},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "f1", "delta": item["arguments"]},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": {"status": "completed", "output": [item]}}
            ]:
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                self.wfile.flush()
        else:
            data = json.dumps({"status": "completed", "output": [], "fixture": "OK"}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upstream.received = []
        self.start_server(self.upstream)
        self.settings = {"routes": {slug: {"base_url": f"http://127.0.0.1:{self.upstream.server_port}/{kind}"}
                                   for slug, (_, kind) in router.COMPANY_ROUTES.items()}, "tool_search": True}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), router.Handler)
        self.server.router_secret = "fixture-router-secret-at-least-24-characters"
        self.server.settings = self.settings
        self.server.active_requests = 0
        self.server.activity_lock = threading.Lock()
        self.events = []
        self.server.event = self.events.append
        self.server.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), router.NoRedirect())
        self.start_server(self.server)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def start_server(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        def finish():
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()
        self.addCleanup(finish)

    def post(self, payload, token=True, path="/v1/responses"):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer openai-fixture-credential",
                   "Cookie": "private-fixture-cookie", "ChatGPT-Account-ID": "private-fixture-account"}
        if token:
            headers["X-Codex-Router-Token"] = self.server.router_secret
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}" + path,
                                         data=json.dumps(payload).encode(), headers=headers)
        with self.opener.open(request, timeout=3) as response:
            return response.read()

    def test_both_models_use_exact_configured_routes_without_openai_headers(self):
        for slug, (_, route) in router.COMPANY_ROUTES.items():
            self.assertEqual(json.loads(self.post({"model": slug, "input": "hello"}))["fixture"], "OK")
            path, headers, payload = self.upstream.received[-1]
            self.assertEqual(path, f"/{route}/responses")
            self.assertEqual(payload["model"], slug)
            lowered = {key.lower() for key in headers}
            self.assertFalse(lowered & {"authorization", "cookie", "chatgpt-account-id", "x-codex-router-token"})
        self.assertTrue(all("hello" not in json.dumps(e) for e in self.events))
        self.assertTrue(any(e.get("transport_complete") for e in self.events))

    def test_authentication_and_malformed_requests_do_not_reach_upstream(self):
        for payload, token, code in [({"model": router.GLM_MODEL}, False, 403),
                                      ([], True, 400), ({"model": []}, True, 400),
                                      ({"model": "unknown"}, True, 400)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(payload, token)
            self.assertEqual(error.exception.code, code)
            error.exception.close()
        self.assertFalse(self.upstream.received)

    def test_streaming_search_conversion_over_real_http(self):
        payload = {"model": router.DEEPSEEK_MODEL, "input": "find fixture", "stream": True,
                   "tools": [{"type": "tool_search", "execution": "client", "parameters": {
                       "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}]}
        stream = self.post(payload).decode()
        self.assertIn('"type": "tool_search_call"', stream)
        self.assertNotIn("response.function_call_arguments.delta", stream)
        self.assertIn("response.completed", stream)
        sent = self.upstream.received[-1][2]
        self.assertEqual(sent["tools"][0]["type"], "function")
        self.assertEqual(sent["tools"][0]["name"], "codex_local_tool_search")

    def test_gpt_uses_pinned_url_and_no_tool_search_adapter(self):
        captured = []
        class Response(io.BytesIO):
            code = 200
            headers = {"Content-Type": "text/event-stream"}
        raw = b'data: {"type":"response.completed","fixture":"GPT passthrough"}\n\n'
        class Opener:
            def open(self, request, timeout):
                captured.append(request)
                return Response(raw)
        self.server.opener = Opener()
        payload = {"model": "gpt-fixture", "input": "hello", "tools": [{"type": "tool_search"}]}
        self.assertEqual(self.post(payload), raw)
        request = captured[0]
        self.assertEqual(request.full_url, router.OPENAI_BASE + "/responses")
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(request.get_header("Authorization"), "Bearer openai-fixture-credential")
        self.assertIsNone(request.get_header("X-codex-router-token"))

    def test_company_api_key_comes_only_from_explicit_environment(self):
        self.settings["routes"][router.GLM_MODEL]["api_key_env"] = "FIXTURE_GLM_KEY"
        with patch.dict(os.environ, {"FIXTURE_GLM_KEY": "upstream-fixture-credential"}):
            self.post({"model": router.GLM_MODEL})
        headers = self.upstream.received[-1][1]
        self.assertEqual(headers["Authorization"], "Bearer upstream-fixture-credential")

    def test_compaction_endpoint_is_routed(self):
        self.post({"model": router.GLM_MODEL, "input": []}, path="/v1/responses/compact")
        self.assertEqual(self.upstream.received[-1][0], "/glm/responses/compact")


class SettingsTests(unittest.TestCase):
    def test_example_requires_real_upstreams(self):
        with self.assertRaises(ValueError):
            router.load_settings(Path(__file__).resolve().parents[1] / "config/router.example.json")

    def test_embedded_credentials_and_url_suffixes_rejected(self):
        base = {"routes": {slug: {"base_url": "https://gateway.example/v1"} for slug in router.COMPANY_ROUTES}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for url in ["https://user:password@gateway.example/v1", "https://gateway.example/v1?token=private", "file:///tmp/socket"]:
                data = copy.deepcopy(base)
                data["routes"][router.GLM_MODEL]["base_url"] = url
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    router.load_settings(path)


if __name__ == "__main__":
    unittest.main()
