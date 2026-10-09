import unittest
from router import select_route, normalize_history, OPENAI_BASE, GLM_BASE, DEEPSEEK_BASE, DEEPSEEK_MODEL

class RoutingTests(unittest.TestCase):
    def test_glm_never_receives_openai_credentials(self):
        base, headers, route = select_route("glm-5.3-flash", {
            "Authorization": "Bearer test-secret", "Cookie": "private",
            "ChatGPT-Account-ID": "private-account", "X-Codex-Router-Token": "local-secret",
            "Session-ID": "private-session", "Content-Type": "application/json"})
        self.assertEqual(base, GLM_BASE)
        self.assertEqual(route, "glm")
        self.assertEqual(set(headers), {"Content-Type", "Accept", "Accept-Encoding"})

    def test_gpt_credentials_go_only_to_pinned_openai_url(self):
        base, headers, route = select_route("gpt-6-astra", {
            "Authorization": "Bearer test-secret", "Host": "attacker.example",
            "X-Codex-Router-Token": "local-secret", "Proxy-Authorization": "private"})
        self.assertEqual(base, OPENAI_BASE)
        self.assertEqual(headers.get("Authorization"), "Bearer test-secret")
        self.assertNotIn("Host", headers)
        self.assertNotIn("X-Codex-Router-Token", headers)
        self.assertNotIn("Proxy-Authorization", headers)

    def test_unconfigured_models_are_rejected(self):
        with self.assertRaises(ValueError):
            select_route("https://attacker.example", {})

    def test_deepseek_exact_route_and_credential_isolation(self):
        base, headers, route = select_route(DEEPSEEK_MODEL, {
            "Authorization": "Bearer test-secret", "authorization": "lowercase-secret",
            "Cookie": "private", "ChatGPT-Account-ID": "private-account",
            "X-Codex-Router-Token": "local-secret", "Session-ID": "private-session"})
        self.assertEqual((base, route), (DEEPSEEK_BASE, "deepseek"))
        self.assertEqual(set(headers), {"Content-Type", "Accept", "Accept-Encoding"})
        for unknown in ("deepseek-v4.1-flash", DEEPSEEK_MODEL + "-unknown"):
            with self.assertRaises(ValueError):select_route(unknown, {})

    def test_cross_provider_history_preserves_messages_and_tool_linkage(self):
        payload = {"reasoning": {"effort": "max"}, "input": [
            {"type": "reasoning", "id": "glm-rs", "summary": []},
            {"type": "reasoning", "id": "openai-rs", "encrypted_content": "opaque-test"},
            {"type": "message", "id": "foreign-message", "role": "assistant", "content": "hello"},
            {"type": "function_call", "id": "foreign-call", "call_id": "call-1", "name": "shell", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call-1", "output": "OK"}]}
        for route in ("glm", "deepseek", "openai"):
            self.assertEqual(normalize_history(payload, route)["reasoning"], {"effort": "max"})
            result = normalize_history(payload, route)["input"]
            self.assertTrue(all("id" not in item for item in result))
            self.assertEqual(result[-1]["call_id"], result[-2]["call_id"])
            self.assertEqual(result[-3]["content"], "hello")
            self.assertEqual(sum(item["type"] == "reasoning" for item in result), 1 if route == "openai" else 0)
        self.assertEqual(payload["input"][0]["id"], "glm-rs")

if __name__ == "__main__":
    unittest.main()
