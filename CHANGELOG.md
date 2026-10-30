# Changelog

## 0.4.1 — 2026-10-30

- Update the GLM model ID from `glm-5.3-flash` to `glm-5-3-flash-260828`.
- Keep historical validation evidence and old model-name backups unchanged.
- Clarify that the repository contains placeholder gateway URLs only; real deployment URLs remain machine-local.

## 0.4.0 — 2026-10-28

- Update the DeepSeek model ID from `deepseek-v4-1-flash-260910` to `deepseek-v4-1-flash-261017`.
- Serve a provider-scoped dynamic model catalog from the loopback router.
- Read GPT entries from the installed Codex bundled catalog at startup, so Codex upgrades add new GPT models without regenerating a static menu.
- Append only GLM and DeepSeek templates; cache the last successful merged catalog for transient refresh failures.
- Add dynamic-catalog, binary upgrade, cache fallback, and isolated-install regressions.


## 0.3.0 — 2026-10-09

- Consolidate the three source conversations into one portable project.
- Preserve GPT / GLM / DeepSeek routing, history compatibility and the optional workflow skill.
- Include the final 1M / 95% external-model catalog and client tool-search adapter.
- Externalize gateway URLs and optional API key environment names; exclude machine state.
- Add a unified macOS installer, guarded merge restore and reversible tool-search switch.
- Add local HTTP and lifecycle regressions, CI, source provenance and validation boundaries.

The bundled workflow retains its own 0.2.0 version and historical validation record.
