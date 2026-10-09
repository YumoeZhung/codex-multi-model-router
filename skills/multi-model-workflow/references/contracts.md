# Packets, decisions and configuration

All paths passed to commands should be absolute; source evidence and allowed paths are repo-relative. Keep artifacts outside the target checkout. `start` requires an existing HEAD commit. Default base is HEAD, which reviews current tracked staged/unstaged changes; use an explicit base for committed changes. Untracked non-ignored files are fingerprinted and can be read, but are not in `diff.patch`. Ignored outputs and external services are outside the fingerprint; source fingerprinting is not a complete filesystem audit.

## Review packet

```json
{
  "task": "Review the change against the requirement: zero is a valid price. Inspect original source and call sites; find omissions as well as challenging my claims.",
  "questions": [{"id": "F1", "claim": "The fallback may replace a valid zero price."}],
  "allowed_paths": ["pricing.py"],
  "acceptance": ["Each claim has a concrete trigger and observed or source-proven consequence."]
}
```

`questions` can be empty for independent exploration. Main questions use unique `F<number>` IDs. The child must address every known ID and uses `R<round>-N<number>` for additions. Its structured output schema is generated in each round. Cited quotes must match the actual source line; this verifies citation accuracy, not the inference. Read-only review may inspect relevant callers beyond `allowed_paths`; in task mode that field is the permitted modification scope.

## Main review decision

```json
{
  "conclusions": [{"id": "F1", "status": "confirmed", "reason": "Verified with price=0; fallback changes the value.", "evidence": [{"path": "pricing.py", "line": 2, "quote": "return price or 100"}]}],
  "continue": false,
  "feedback": "",
  "checks": ["Executed a reproduction with price=0; received 100."]
}
```

Main conclusions may include `priority` (P0–P3); reassess severity independently rather than inheriting the assistant label.

Statuses: `confirmed`, `dismissed`, `unresolved`. Adjudicate every ID from `state.json.questions`. Confirmed findings require source evidence. Only claim checks actually performed; `checks` is a main-agent record, not script-executed verification. `continue: true` requires focused feedback, remaining budget, and new source evidence in review mode. Every round's judgment is retained.

## Task packet and acceptance

```json
{
  "task": "Implement clamp(value, low, high). Raise ValueError for low > high. Do not change public signatures or dependencies.",
  "questions": [],
  "allowed_paths": ["math_utils.py", "tests/test_math_utils.py"],
  "acceptance": ["Boundary and reversed-range tests pass", "No changes outside the two allowed files"]
}
```

Task mode defaults to read-only, allowing bounded evidence collection or batch execution without code edits. For writes, add `--allow-write` and use a clean isolated checkout. A main task decision uses exactly one ID `TASK`: `confirmed` when acceptance is independently verified, `unresolved` when blocked/incomplete, `dismissed` when rejected. A confirmed TASK needs a source citation and nonempty main verification record. Set `continue: true` with focused repair instructions for another bounded attempt. At a limit, leave changes isolated and report failed criteria. Integrating accepted changes is a separate action authorized by the enclosing task.

## Model-neutral configuration

Bundled `defaults.json` is a starter default, not a restriction to a vendor. Optional host-local JSON:

```json
{
  "assistant_model": "exact-model-id-from-your-catalog",
  "assistant_effort": "high",
  "max_rounds": 3,
  "call_timeout_seconds": 300,
  "total_timeout_seconds": 1200,
  "codex_binary": "/absolute/path/to/codex"
}
```

Only set fields you override. Save at `$CODEX_HOME/multi-model-workflow.json`, or use `start --config <file>`. To list available IDs:

```sh
python3 <skill-dir>/scripts/workflow.py models
```

No DeepSeek ID is predeclared: add it to your host normally, verify the actual catalog ID and capability, then select it here. Effort must be supported by that model; failure is surfaced instead of weakening the setting silently. Changing defaults affects new runs only; each existing run freezes its configuration.

The runner preserves the host's provider/auth configuration and never reads or writes auth files. It records requested model and any top-level model IDs supplied in CLI events. Some CLI versions expose no runtime model in events: then observed_models stays empty and actual routing must be verified through the host/provider log. `usage` is the CLI's token report, not a prediction of subscription quota or monetary savings.
