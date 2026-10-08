# Project Instructions

Before planning or implementing the schema-adaptive Text-to-SQL work, read `docs/schema-adaptive-text-to-sql-plan.md`.

Treat that document as the repository-backed project memory and design source of truth. When implementation, scope, dataset policy, model strategy, evaluation design, or major decisions change:

1. update the relevant section of the document;
2. add or amend an ADR when the decision is significant;
3. update roadmap checkboxes when work is completed;
4. append a dated entry to the change log.

Preserve the existing legacy system as a baseline unless the user explicitly approves its removal or replacement.

Independent review: after implementing a phase, run a review-only pass with Luna through opencode, then reproduce each finding and fix only the confirmed ones with regression tests. Always close stdin and set a time limit:

```bash
timeout 1200 opencode run --pure -m opencode-go/gpt-6-luna --variant high --agent plan --dir <repo> "<review-only prompt>" < /dev/null
```

`opencode run` reads a piped stdin until EOF before it starts, so an input that never closes stalls it before any model call (seen 2026-10-06 and 2026-10-08). Do not chain it after a heredoc in the same command. If it produces no output, check `~/.local/share/opencode/log/opencode.log` and stop leftover `opencode.exe` processes.

Current development policy (user decision, 2026-10-01): no real school database will be obtained. Development and evaluation use only the team-built legacy DB (from a previous semester's course catalog spreadsheet shared with students) and databases imitated from other schools' public course information. Under that premise: E3 allows using the legacy data and its derived datasets without publishing the original spreadsheet or DB dump; L5 allows demonstrations (state the data's origin; blur professor names in material published outside the school); L2 is the product's own admin review screen, not a separate gate; L1/L3 are reviewed by the user and teammates before final model-performance claims; L4 still needs people outside development. Evaluation databases for unseen-school claims are imitated by teammates, with questions and answers fixed by them, and are not seen by the agent before evaluation. If real school data is ever obtained, the user will say so and these decisions are revisited. Record user decisions as such; never mark a human checkpoint approved on the agent's own authority. See `docs/human-validation-checkpoints.md`.
