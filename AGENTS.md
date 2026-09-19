# Project Instructions

Before planning or implementing the schema-adaptive Text-to-SQL work, read `docs/schema-adaptive-text-to-sql-plan.md`.

Treat that document as the repository-backed project memory and design source of truth. When implementation, scope, dataset policy, model strategy, evaluation design, or major decisions change:

1. update the relevant section of the document;
2. add or amend an ADR when the decision is significant;
3. update roadmap checkboxes when work is completed;
4. append a dated entry to the change log.

Preserve the existing legacy system as a baseline unless the user explicitly approves its removal or replacement.

Current development policy: finish synthetic-data implementation and verification before asking the team to decide human validation checkpoints. Keep E3/L5 and L1/L3 pending; do not repeatedly request early decisions or block synthetic local development on them. Apply E3 before additional use or sharing of actual CNU data, L5 before actual-data deployment or external demonstration, and L1/L3 before final model-performance claims. Never mark a human checkpoint approved on the agent's own authority.
