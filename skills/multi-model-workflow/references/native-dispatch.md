# Native dispatch and reversible compatibility

Choose one transport per run before work starts. Prefer `--transport native` when the host can select the exact model, the task delivery/required tools have passed a harmless probe, and host-managed interruption is sufficient. Use `--transport cli` explicitly when a hard subprocess timeout or shell sandbox is required, or the native task/tools are incompatible. Never launch both for the same subtask, inherit a model accidentally, or silently fail over after a dispatched task fails. Strong-model subtasks remain valid when the task needs their reasoning or tools; do not set every global subagent to the cheapest model.

## Native workflow

1. Start with `workflow.py start ... --transport native` (same packet/model/base contract as CLI). The current host needs the installed optional bridge for registered native task delivery.
2. Reserve the round with `workflow.py ask --run <dir>`. A nested orchestrator supplies `--agent-parent <its-actual-canonical-path>`; the default is `/root`. The runner generates an unpredictable `mmw_<uuid>` child name and returns its full path. Use it exactly. Generic names are not accepted by the bridge, so another chat's `/root/worker` cannot collide. This writes the task prompt/schema, deadline and task mirror; it does not spawn anything.
3. Read the returned `prompt_file`. Call the native spawn tool **once**, with that exact text, requested model/effort, and a fresh/empty context (`fork_turns="none"` on this host). Use exactly the reserved task name. Do not copy the full conversation. The returned native agent path must match the reservation.
4. Wait on that same agent. Check its actual state at most every 60 seconds while reporting meaningful progress. The parent must interrupt it at the deadline; the ledger cannot terminate native agents itself. Never treat an observation timeout as agent completion.
5. Once the host confirms terminal completion, preserve the raw response and save its JSON result to an external file and call:
   `workflow.py native-result --run <dir> --agent-path <exact-path> --terminal-confirmed --result <json>`.
   For failure/interrupt, use `--error <reason>` instead. The intake closes registration, validates exact source evidence/scope, rejects late results, and awaits main judgment. It does not retry or switch models.
   If only Markdown wrapping needs removal, record the exact formatting-only normalization and keep all JSON values unchanged. Malformed JSON or a substantive repair is a failed answer, not something the main agent should silently fix.
6. Main verifies and calls `judge` exactly as with CLI. If continuing, `ask` generates a fresh native agent name for the next round. The prior agent's work must be terminal before another round starts.

`awaiting_dispatch` means a round is reserved and may already have a live agent. After an interrupted parent turn, inspect the native agent inventory before spawning anything. If abandoning a run, first verify/stop all workers, then use `abandon --no-live-workers --reason ...`; this preserves changes and marks the run incomplete. These confirmations record the main agent's host checks, not a cryptographic proof.

Native workers inherit the host's permissions; this host does not expose a per-spawn sandbox flag. Read-only here is a task instruction plus post-run source audit, not OS enforcement. CLI transport remains available for enforced shell read-only policy. Neither mode is a container or a full external-side-effect audit.

## Why the optional bridge exists

On the tested host, native tasks arrive as `agent_message` with platform-owned encrypted task content. GPT could consume this representation; a GLM native probe reached its provider but only acknowledged AGENTS instructions. The existing model router did not translate this item. This is task-transport compatibility evidence, not a model-quality benchmark.

The optional `native_bridge.py` wraps the existing router, importing its original routing/auth code unchanged. For an **exact registered model + sender + generated recipient + first message identity**, it supplies a copy of the task text that the main agent itself authored. It does not decrypt platform content, recover hidden reasoning, process unregistered native tasks, or change normal menu conversations. The first identity binds on delivery; the host exposes no separate chat ID here, so randomly generated 128-bit names prevent ordinary cross-chat path collisions. This is not a security boundary against a process able to read the private registry. Registrations expire and are closed after confirmed completion. A second assignment to the same agent is rejected; use a fresh registered agent for each round. Registrations live under `$CODEX_HOME/multi-model-workflow-runtime/requests`, outside repositories and with a private parent directory. Keep them local.

Install only on a compatible existing macOS Python-router LaunchAgent using `manage.py install-bridge --service <plist>`. The installer saves its original service file, changes only ProgramArguments, and keeps a field-ownership manifest. It checks local service health after restart and rolls back startup failures. Interrupted operations reconcile the actual service command before removing runtime files. Local health alone does not prove provider connectivity. Never patch the router's source or model/provider/login configuration to make this skill work. New provider/model/tool combinations still need live probes; API compatibility is not assumed from the model name.

## Uninstall and restore

Use `manage.py uninstall` after all native/CLI workers are terminal and unfinished runs have been finished or explicitly abandoned. The command:

- restores only the service ProgramArguments it owns, preserving later environment/proxy edits;
- removes only its exact marked delegation paragraph from personal AGENTS.md, preserving other instructions;
- leaves router source, service, model catalog, Codex config and login data intact;
- archives the skill (including Git history), its local configuration, runtime records and default run records outside skill discovery; it does not delete evidence;
- stops if an owned field was edited later, rather than overwriting that edit.

Archive paths are checked against every moved directory before changes. An uninstall journal records the selected archive and can resume after partial moves; rerun the recovery command without `--archive` to reuse that transaction. External custom output folders remain in place; an index still checks their unfinished run state. Missing ledgers stop removal until workers and records are accounted for.

The installed runtime also retains `remove_skill.py` as a recovery entry point if the skill folder is removed manually. Prefer the proper uninstaller to manual deletion. Start a new Codex chat after uninstall so the old skill/instructions are no longer loaded. User-selected output folders outside the default run directory are intentionally left where they are.

`remove-bridge` restores the original router service while retaining the skill and records. `remove-policy` removes only the managed delegation paragraph. These are useful for targeted rollback. They do not alter the manually selectable models.
