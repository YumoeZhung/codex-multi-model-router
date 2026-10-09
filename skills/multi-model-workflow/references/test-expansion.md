# Expand main-designed tests with auxiliary models

The main agent supplies the behavioral specification, a few high-quality examples, and a coverage matrix. The auxiliary model fills missing cases; it does not choose the specification or certify its own tests. Use task mode with writes restricted to new/explicit test files. Keep production source and golden examples outside allowed_paths.

## Packet contents

- Intended behavior and stable API, including invalid input behavior where specified.
- 2–5 main-designed exemplar tests with clear inputs, expected results, and why each matters.
- Missing equivalence classes and boundaries; target semantic coverage, not a quota of near-duplicate cases.
- Repository test command and allowed output files.
- Prohibit changing production behavior, exemplar assertions, fixtures that determine expected results, or dependencies to make tests pass.
- State uncertain behavior as an open question. Do not let the assistant derive the oracle by calling the same implementation it is testing.

## Main-agent acceptance

1. Read added tests. Check oracle against the specification, meaningful distinct cases, and proper discovery. Reject assertion-free tests, unconditional mocks, blanket exception catches, excessive skips/xfails, and renamed duplicates.
2. Run the full relevant test set independently. A green run only proves agreement with the present implementation.
3. Where practical, run the added tests against a few deliberate plausible faults in an isolated fixture or in-memory substitute (e.g., off-by-one, zero treated as absent, missing reversed-range check). A test suite that remains green for those faults may miss the intended behavior. Do not mutate production code in the user's checkout for this check.
4. Preserve selected high-value cases, reject low-value duplication, and record covered and uncovered behavior. Mutation checks demonstrate sensitivity to selected faults, not complete correctness.

When comparing two auxiliary models, give each the same approved examples in separate clean checkouts and deduplicate the results. Do not multiply calls unless the user asks for both. Models stay configurable; no vendor-specific prompts.
