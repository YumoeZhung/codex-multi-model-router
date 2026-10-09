#!/usr/bin/env python3
"""Portable macOS lifecycle manager. No auth.json access or application patching."""
import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request

from router import load_settings, COMPANY_ROUTES

SOURCE = Path(__file__).resolve().parent
PROVIDER = "multi_model_router"
LABEL = "io.github.codex-multi-model-router"
OWNED_KEYS = ("model", "model_provider", "model_catalog_json", "model_reasoning_summary")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def atomic_write(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def split_root(text):
    match = re.search(r"(?m)^[ \t]*\[", text)
    pos = match.start() if match else len(text)
    return text[:pos], text[pos:]


def edit_root(text, values):
    root, rest = split_root(text)
    if root and not root.endswith("\n"):
        root += "\n"
    for key, value in values.items():
        pattern = r"(?m)^[ \t]*" + re.escape(key) + r"[ \t]*=[^\n]*(?:\n|$)"
        line = "" if value is None else key + " = " + json.dumps(value, ensure_ascii=False) + "\n"
        if re.search(pattern, root):
            root = re.sub(pattern, lambda _: line, root)
        else:
            root += line
    return root + rest


def configured_text(original, catalog, secret, port, default):
    values = {"model": default, "model_provider": PROVIDER,
              "model_catalog_json": str(catalog), "model_reasoning_summary": "none"}
    provider = {"name": "GPT + GLM + DeepSeek", "base_url": f"http://127.0.0.1:{port}/v1",
                "wire_api": "responses", "requires_openai_auth": True, "supports_websockets": False,
                "http_headers": {"X-Codex-Router-Token": secret}}
    block = f"\n[model_providers.{PROVIDER}]\n"
    for key, value in provider.items():
        if key == "http_headers":
            block += 'http_headers = { "X-Codex-Router-Token" = ' + json.dumps(secret) + " }\n"
        else:
            block += key + " = " + json.dumps(value) + "\n"
    result = edit_root(original, values) + block
    expected = tomllib.loads(original)
    expected.update(values)
    expected.setdefault("model_providers", {})[PROVIDER] = provider
    if tomllib.loads(result) != expected:
        raise ValueError("Unsupported TOML layout; configuration was not modified")
    return result


def restored_text(current, original, installed):
    before, baseline, expected = map(tomllib.loads, (current, original, installed))
    for key in OWNED_KEYS:
        if before.get(key) != expected.get(key):
            raise ValueError("Owned setting changed after install: " + key)
    if before.get("model_providers", {}).get(PROVIDER) != expected["model_providers"][PROVIDER]:
        raise ValueError("Router provider changed after install; merge manually")
    for profile in before.get("profiles", {}).values():
        if profile.get("model_provider") == PROVIDER:
            raise ValueError("A profile still selects the router; update it before restoring")
    result = edit_root(current, {key: baseline.get(key) for key in OWNED_KEYS})
    pattern = r"(?ms)^\[model_providers\." + PROVIDER + r"\][^\n]*\n.*?(?=^\[|\Z)"
    result, count = re.subn(pattern, "", result)
    if count != 1:
        raise ValueError("Router provider table cannot be safely removed")
    wanted = copy.deepcopy(before)
    for key in OWNED_KEYS:
        if key in baseline:
            wanted[key] = baseline[key]
        else:
            wanted.pop(key, None)
    wanted["model_providers"].pop(PROVIDER)
    if not wanted["model_providers"] and "model_providers" not in baseline:
        wanted.pop("model_providers")
    if tomllib.loads(result) != wanted:
        raise ValueError("Restore would change unrelated settings; merge manually")
    return result


def build_catalog(gpt_models, settings, source=SOURCE):
    models = copy.deepcopy(gpt_models)
    for slug in COMPANY_ROUTES:
        model = json.loads((source / "models" / (slug + ".json")).read_text())
        model["supports_search_tool"] = settings.get("tool_search", True)
        for key in ("context_window", "max_context_window"):
            model[key] = settings.get("context_window", 1048576)
        model["effective_context_window_percent"] = settings.get("effective_context_window_percent", 95)
        models.append(model)
    return {"models": models}


def domain():
    return "gui/" + str(os.getuid())


class Service:
    def start(self, plist):
        subprocess.run(["launchctl", "bootstrap", domain(), str(plist)], check=True, capture_output=True)

    def stop(self):
        result = subprocess.run(["launchctl", "bootout", domain() + "/" + LABEL], capture_output=True, text=True)
        if result.returncode and not any(s in result.stderr for s in ("No such process", "Could not find service")):
            raise RuntimeError("Could not stop the router service")

    def health(self, port):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(40):
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=.5) as response:
                    if json.load(response) == {"status": "ok", "service": "codex-model-router"}:
                        return
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        raise RuntimeError("Router failed its local health check")

    def idle(self, port, secret):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status", headers={"X-Codex-Router-Token": secret})
        with opener.open(req, timeout=2) as response:
            if json.load(response)["active_requests"]:
                raise RuntimeError("Router has active requests; retry after tasks finish")


class Manager:
    def __init__(self, codex_home, launch_agents=None, service=None):
        self.home = Path(codex_home).expanduser().resolve()
        self.target = self.home / "multi-model-router"
        self.config = self.home / "config.toml"
        self.plist = Path(launch_agents or Path.home() / "Library/LaunchAgents") / (LABEL + ".plist")
        self.service = service or Service()

    def preflight(self, settings_path, port):
        if platform.system() != "Darwin":
            raise RuntimeError("The launchd installer supports macOS only")
        if not 1024 <= port <= 65535:
            raise ValueError("Choose an unprivileged TCP port")
        settings = load_settings(settings_path)
        if self.target.exists() or self.plist.exists():
            raise ValueError("Installation directory or service already exists; preserve it and inspect status")
        raw = self.config.read_text()
        config = tomllib.loads(raw)
        if config.get("model_provider", "openai") != "openai" or config.get("model_catalog_json"):
            raise ValueError("Existing custom provider/catalog requires an explicit migration; nothing changed")
        if PROVIDER in config.get("model_providers", {}):
            raise ValueError("Router provider name already exists")
        cache = json.loads((self.home / "models_cache.json").read_text())
        models = [m for m in cache["models"] if m.get("slug", "").startswith("gpt-")]
        if not models:
            raise ValueError("Run native GPT once to populate this account's own model cache")
        default = config.get("model") or models[0]["slug"]
        if default not in [m["slug"] for m in models]:
            raise ValueError("Default GPT model is not in this machine's model cache")
        for route in settings["routes"].values():
            env = route.get("api_key_env")
            if env and not os.environ.get(env):
                raise ValueError("Required API key environment variable is missing: " + env)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return raw, settings, models, default

    def install(self, settings_path, port=17864, proxy=None):
        raw, settings, models, default = self.preflight(settings_path, port)
        secret = secrets.token_urlsafe(32)
        updated = configured_text(raw, self.target / "models.json", secret, port, default)
        backup = self.home / ("config.toml.before-multi-model-router-" + stamp())
        atomic_write(backup, raw.encode())
        self.target.mkdir(mode=0o700)
        config_written = False
        service_attempted = False
        try:
            for name in ("router.py", "tool_search_adapter.py"):
                shutil.copy2(SOURCE / name, self.target / name)
            atomic_write(self.target / "router.secret", secret.encode())
            write_json(self.target / "router.local.json", settings)
            write_json(self.target / "models.json", build_catalog(models, settings))
            atomic_write(self.target / "config.installed.toml", updated.encode())
            env = {"PYTHONUNBUFFERED": "1", "NO_PROXY": "localhost,127.0.0.1,::1"}
            proxies = urllib.request.getproxies()
            for kind in ("http", "https"):
                value = proxy or proxies.get(kind)
                if value:
                    env[kind.upper() + "_PROXY"] = value
            for route in settings["routes"].values():
                key = route.get("api_key_env")
                if key:
                    env[key] = os.environ[key]
            plist = {"Label": LABEL, "ProgramArguments": [sys.executable, str(self.target / "router.py"),
                     "--port", str(port), "--settings", str(self.target / "router.local.json"),
                     "--secret-file", str(self.target / "router.secret"), "--log-file", str(self.target / "events.jsonl")],
                     "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
                     "WorkingDirectory": str(self.target), "EnvironmentVariables": env,
                     "StandardOutPath": str(self.target / "stdout.log"), "StandardErrorPath": str(self.target / "stderr.log")}
            self.plist.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self.plist, plistlib.dumps(plist))
            state = {"version": 1, "active": True, "backup": str(backup), "backup_sha256": digest(backup.read_bytes()),
                     "installed_config_sha256": digest(updated.encode()), "port": port, "plist": str(self.plist),
                     "original_argv": plist["ProgramArguments"]}
            write_json(self.target / "installation.json", state)
            service_attempted = True
            self.service.start(self.plist)
            self.service.health(port)
            if self.config.read_text() != raw:
                raise RuntimeError("Codex configuration changed concurrently; install cancelled")
            atomic_write(self.config, updated.encode())
            config_written = True
        except BaseException:
            if service_attempted:
                self.service.stop()
            if config_written and self.config.read_text() == updated:
                atomic_write(self.config, raw.encode())
            self.plist.unlink(missing_ok=True)
            shutil.rmtree(self.target)
            raise
        return {"installed": True, "local_health": True, "upstream_verified": False,
                "backup": str(backup), "restart_codex": True}

    def state(self):
        state = json.loads((self.target / "installation.json").read_text())
        if Path(state["plist"]) != self.plist:
            raise ValueError("Recorded service location differs; use the original installation")
        return state

    def require_original_service(self, state):
        if not state.get("active"):
            raise ValueError("Router already restored; retained directory contains backups")
        plist = plistlib.loads(self.plist.read_bytes())
        if plist.get("ProgramArguments") != state["original_argv"]:
            raise ValueError("Service is wrapped or modified; remove the optional native bridge first")
        self.service.idle(state["port"], (self.target / "router.secret").read_text().strip())

    def plan_restore(self):
        state = self.state()
        backup = Path(state["backup"])
        if digest(backup.read_bytes()) != state["backup_sha256"]:
            raise ValueError("Original configuration backup changed")
        installed = (self.target / "config.installed.toml").read_bytes()
        if digest(installed) != state["installed_config_sha256"]:
            raise ValueError("Installed configuration snapshot changed")
        current = self.config.read_text()
        restored = restored_text(current, backup.read_text(), installed.decode())
        return state, current, restored

    def restore(self):
        state, current, restored = self.plan_restore()
        self.require_original_service(state)
        archive = self.target / ("restore-" + stamp())
        archive.mkdir(mode=0o700)
        atomic_write(archive / "config.toml", current.encode())
        atomic_write(archive / "service.plist", self.plist.read_bytes())
        if self.config.read_text() != current:
            raise RuntimeError("Configuration changed concurrently; rerun restore")
        atomic_write(self.config, restored.encode())
        try:
            self.service.stop()
        except BaseException:
            if self.config.read_text() == restored:
                atomic_write(self.config, current.encode())
            raise
        self.plist.unlink()
        state["active"] = False
        write_json(self.target / "installation.json", state)
        return {"restored": True, "retained_backups": str(self.target), "restart_codex": True}

    def tool_search(self, enabled):
        state = self.state()
        self.require_original_service(state)
        config_path, catalog_path = self.target / "router.local.json", self.target / "models.json"
        old_settings, old_catalog = config_path.read_bytes(), catalog_path.read_bytes()
        settings, catalog = json.loads(old_settings), json.loads(old_catalog)
        settings["tool_search"] = enabled
        for model in catalog["models"]:
            if model.get("slug") in COMPANY_ROUTES:
                model["supports_search_tool"] = enabled
        archive = self.target / ("tool-search-before-" + stamp())
        archive.mkdir(mode=0o700)
        atomic_write(archive / config_path.name, old_settings)
        atomic_write(archive / catalog_path.name, old_catalog)
        self.service.stop()
        try:
            write_json(config_path, settings)
            write_json(catalog_path, catalog)
            self.service.start(self.plist)
            self.service.health(state["port"])
        except BaseException:
            self.service.stop()
            atomic_write(config_path, old_settings)
            atomic_write(catalog_path, old_catalog)
            self.service.start(self.plist)
            self.service.health(state["port"])
            raise
        return {"tool_search": enabled, "gpt_catalog_unchanged": True, "restart_codex": True}

    def install_skill(self):
        destination = self.home / "skills/multi-model-workflow"
        if destination.exists():
            raise ValueError("Existing workflow skill preserved; review differences before upgrading")
        shutil.copytree(SOURCE / "skills/multi-model-workflow", destination,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return {"skill": str(destination), "bridge_installed": False, "global_policy_installed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "install", "status", "plan-restore", "restore",
                                           "enable-tool-search", "disable-tool-search", "install-skill"])
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--port", type=int, default=17864)
    parser.add_argument("--proxy", help="Optional HTTP proxy URL; otherwise use this machine's proxy settings")
    args = parser.parse_args()
    manager = Manager(args.codex_home)
    try:
        if args.action in ("check", "install") and args.settings is None:
            raise ValueError("--settings is required")
        if args.action == "check":
            _, _, models, default = manager.preflight(args.settings, args.port)
            result = {"ready": True, "default_model": default, "gpt_models_preserved": len(models)}
        elif args.action == "install":
            result = manager.install(args.settings, args.port, args.proxy)
        elif args.action == "restore":
            result = manager.restore()
        elif args.action == "plan-restore":
            state, _, _ = manager.plan_restore()
            result = {"merge_ready": True, "active": state["active"], "preserves_unrelated_settings": True}
        elif args.action.endswith("tool-search"):
            result = manager.tool_search(args.action.startswith("enable"))
        elif args.action == "install-skill":
            result = manager.install_skill()
        else:
            state = manager.state()
            result = {"active": state["active"], "port": state["port"], "installed_at": str(manager.target)}
            if state["active"]:
                manager.service.health(state["port"])
                result["local_health"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
