# Decide when the main agent should delegate

Delegate work to reduce main-model effort only when the handoff plus verification costs less than doing the work directly, while preserving the required quality. A model is not universally strong or weak: use observed task-specific results. Availability or unlimited quota alone is not a reason to delegate.

## Decision gate

The main agent answers these questions before dispatch (briefly record the rationale in the task packet's task text):

1. **Clarity:** Can the objective, inputs, scope, output shape, and acceptance criteria be stated without outsourcing unresolved requirements?
2. **Verifiability:** Can I check the result using source references, deterministic checks, ground truth, or an affordable independent assessment? If verifying requires solving the whole problem again, delegation likely saves little.
3. **Consequence:** Is a wrong result contained and reversible before it affects users or decisions? Keep external actions and high-impact judgment with the main agent.
4. **Handoff cost:** Is the bounded task large enough to justify another model call without copying a huge conversation or repeatedly reconstructing context?

- Clear and cheaply verifiable with contained consequences: delegate execution or structured production.
- Uncertain correctness but useful diversity: delegate **advisory** suggestions/challenges, never authoritative decisions. Code review fits here; main reads the source independently.
- Ambiguous requirements, costly verification, high-impact judgment, or high context dependence: main handles it or first reduces it to a bounded subproblem.
- Deterministic transformation/search/batch execution: prefer existing tools/scripts. Use an auxiliary agent only for adapting or interpreting work that actually needs language/model judgment.
- Tiny task: do directly. Do not create busywork to keep cheaper models occupied.

## Useful work families (examples, not triggers)

| Work family | Auxiliary deliverable | Main agent retains |
| --- | --- | --- |
| Evidence gathering | Call sites, source quotes, dependency/version facts, reproducible observations | Search scope, relevance, consequential inference |
| Candidate generation | Alternative edge cases, hypotheses, test cases or possible fixes | Specification, selection, validity and tradeoffs |
| Independent challenge | Counterexamples, potential omissions, false-positive arguments | Independent review, severity, final judgment |
| Rule-bound transformation | Structured tables, mappings, documentation updates, mechanical edits | Rules, allowed paths, spot checks/diff acceptance |
| Batch execution and triage | Execute an approved matrix, cluster errors with original logs | Test design, stopping budget, root-cause decisions |
| Output organization | Deduplicated issue lists, evidence indexes, summaries linked to raw material | Completeness checks and final conclusions |

Do not proactively split production coding among auxiliary models. If the user explicitly wants bounded implementation, the same gate and verification apply. Keep architecture, hidden assumptions, security decisions, destructive actions, approvals, integration and final acceptance with the main agent. Do not equate more output or more agreeing models with more correctness.

## Delegation contract

Each dispatch includes purpose, raw inputs or accessible source, exclusions, allowed writes (none by default), output shape, concrete acceptance criteria, and budget. The auxiliary agent must disclose uncertainty and blockers; it may not widen scope, call other models, or choose a fallback. Send focused context, while retaining enough raw evidence/access to prevent anchoring to the main agent's summary.

The main agent inspects the actual artifact, runs relevant checks, and then accepts, rejects, or asks for one focused refinement within the run's remaining budget. Review refinements require new evidence. If quality repeatedly falls short or verification costs exceed the benefit, stop delegating that task and complete it with the main agent; explicitly report the incomplete auxiliary outcome first. Do not silently switch the helper's model.

## Calibration

Record task type, auxiliary model, acceptance/rework outcome, important omissions, time, and CLI-reported usage. Establish savings through comparable tasks, not token counts from one run. More auxiliary tokens can still save main-model quota, but the workflow may increase latency and total tokens. Never promise a percentage saved without measured evidence.
