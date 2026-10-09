---
name: multi-model-workflow
description: GPT-led multi-model code review with evidence-based challenge, or bounded simple-task delegation to a configurable auxiliary model. Use when the user requests cross-model review, adversarial review, or delegating routine coding work to save main-model usage.
metadata:
  version: "0.2.0"
---

# Multi-model workflow

The current main agent owns requirements, independent review, and final acceptance. An auxiliary model gathers evidence, challenges findings, or performs bounded mechanical work. Do not proactively split production code implementation among weaker models. Do not move final judgment to the auxiliary agent. The helper never launches a second main-model session.

## Select work and models

First apply [references/delegation-policy.md](references/delegation-policy.md): decide by task clarity, verifiability, error consequence and handoff cost, not a fixed list of examples. Tests are one use case, not the scope of this skill. Prefer deterministic tools for truly mechanical work.

- **Review:** independently read the requirements, raw diff and relevant source first. Form concrete questions. Have the auxiliary agent challenge them and independently look for omissions. Verify its source evidence, severity, and any claimed tests before concluding. Agreement is not proof; retain unresolved disagreement.
- **Task:** delegate only clear, bounded work with concrete acceptance criteria and cheap verification: expanding main-designed test examples, executing a test matrix, collecting results, or a specified mechanical transformation. Delegate production code implementation only when the user explicitly requests that division. Keep architectural choices, ambiguous requirements and high-risk changes with the main agent. Do one-step edits directly when delegation costs more than it saves.
- Default auxiliary model comes from `defaults.json`, optionally overlaid by `$CODEX_HOME/multi-model-workflow.json` (default `~/.codex`). A per-run `--assistant-model <actual-id>` overrides it. Do not hard-code a model in prompts or infer an ID from a display name; use `models` to inspect the host catalog. Future models need configuration, not script changes.
- Keep the existing provider/auth route. Missing model, failure, or timeout stops the run with an explicit limitation, never a fallback to the main model. Catalog presence is not connectivity proof.
- Record the actual current main model in `--main-model`. If it is not known, resolve it from the host before proceeding; do not invent one. The script labels this as host-declared, not runtime proof.
- Apply these selection rules to native delegation too. Prefer verified native dispatch, explicitly choose each subagent's model, and keep strong-model subtasks when their reasoning or tools are needed. This auxiliary runner requires a distinct model; strong-model work may use host-native tools under the same scope, evidence, stopping and acceptance rules. Never globally downgrade all subagents.

## Run

For test expansion, also read [references/test-expansion.md](references/test-expansion.md).

Read [references/contracts.md](references/contracts.md) for packet/decision examples and configuration. Use Python 3.11+ (or 3.9 with pip vendored tomli) and the locally installed Codex CLI.

Read [references/native-dispatch.md](references/native-dispatch.md) before native dispatch or changing the optional compatibility bridge. Choose one transport per run: `native` after model/task/tool compatibility is verified and host interruption is sufficient; `cli` for a subprocess deadline or enforced shell sandbox. Do not dispatch the same work through both or silently switch after failure.

1. Prepare a compact packet **outside** the target repository. Include requirements, scoped paths, acceptance criteria, and main-review questions with stable IDs. Pass raw evidence, not only your summary. The auxiliary agent may read relevant source beyond the diff. Never include credentials or unrelated private material.
2. For review, use the existing checkout and specify the intended `--base` commit/ref. For write-enabled tasks, use a clean isolated checkout; prefer the host's managed worktree tools when available. Account for changes before reusing it. `allowed_paths` is a checked scope contract, not an OS path-level write sandbox. Do not auto-revert out-of-scope changes.
3. Start a run:
   ```sh
   python3 <skill-dir>/scripts/workflow.py start --repo <git-root> --packet <packet.json> --main-model <current-model> --base <base-ref> --transport <native-or-cli>
   ```
   Add `--mode task` for bounded execution/evidence/organization work (read-only by default). Add `--allow-write` only for explicitly authorized file changes. Add `--assistant-model <id>` for a one-run choice. Artifacts default outside the repo in `$CODEX_HOME/multi-model-workflow-runs/`.
4. Run `python3 <skill-dir>/scripts/workflow.py ask --run <run-dir>`. In native mode this only reserves a round and generates a unique task name; follow the native-dispatch instructions to spawn once, wait, confirm completion, and intake the result. In CLI mode it blocks while the child runs: use a live shell session and poll that same session; do not start a duplicate when observation times out. The helper enforces the CLI call timeout. Read `state.json`/the returned result. `ask` is legal only in `ready`; it cannot skip main judgment.
5. **Main agent verifies** actual source and conclusions. In task mode inspect outputs and any diff, check scope, and execute relevant acceptance checks yourself; the child's self-report is not acceptance. Keep unaccepted changes isolated. Do not merge, commit, push or post as a side effect of this skill.
6. Write a decision and run `python3 <skill-dir>/scripts/workflow.py judge --run <run-dir> --decision <decision.json>`. Every known finding needs a conclusion. New findings from the main agent can be added in the final narrative; if they need auxiliary challenge, begin a new explicitly scoped run rather than silently losing them. Continue only with a concrete evidence request or a bounded repair.
7. Repeat `ask` only if state is `ready`. Report `report.md`, confirmed issues, rejected false positives, unresolved items, limitations, and requested/observed models separately. Task `complete` means the main agent finished adjudication; an unresolved TASK is not successful implementation. Review completion is not proof of bug-free code.

## Stop and failure rules

- Main agent can finish early, preserving unresolved items.
- Review cannot continue when a round adds no new cited source evidence; repeated opinions do not count. A new test result should be captured by the main agent as a limitation/verification result; this first version deliberately does not use free-text test claims to bypass the source-evidence stop.
- Default upper bounds: 3 auxiliary calls, 300 seconds per call, 1,200 seconds total wall time (including main-agent work). Configuration can change these. A debate round means one auxiliary call plus one main judgment, not one internal tool call.
- The script stops on a changed review source, invalid/missing answer, failed stream, unexpected model evidence, timeout, or out-of-scope edits. These are incomplete outcomes, not agreement. No automatic retries or model switches.
- If the host interrupts the helper, inspect native agent status or CLI `round-N/process.json` and the actual process. Never treat elapsed time as proof of termination. Do not reset state or start another worker while one lives. After accounting for workers and edits, `abandon --run <dir> --no-live-workers --reason <reason>` records incomplete work.
- CLI children disable configured user MCP servers and use read-only or workspace-write shell sandbox with approvals disabled. Native children inherit host permissions; read-only is an instruction plus post-run audit, not OS enforcement. The host must interrupt native agents at the deadline; intake rejects late results. Neither mode is a container. Do not execute untrusted repo hooks or grant broader tools to rescue a failed review.

For requested comparison of multiple auxiliary models, run separate bounded reviews on the same unchanged source and have the main agent reconcile evidence. Do not add extra model calls by default.

## Reversible installation

The optional bridge only wraps the existing router service; it does not edit router code, provider/auth config, or the model menu. Optional global delegation policy uses an owned paragraph in personal AGENTS.md. Install/remove through `scripts/manage.py`; do not manually patch global configuration.

To uninstall, finish or explicitly abandon runs after confirming all workers stopped, then run `python3 <skill-dir>/scripts/manage.py uninstall`. It restores the original router service command, removes only the owned policy, and archives this skill and its records. Later unrelated settings survive; conflicting owned-field edits stop removal for inspection. Runtime `remove_skill.py` remains available if the skill folder was manually removed. Interrupted uninstall can resume its recorded archive. Start a new chat afterward. Manual menu switching remains available. See native-dispatch for details.
