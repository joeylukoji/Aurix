# AURIX — notes for Claude

- Current version: **MVP 0.1** branch `MVP_0.1`; the evidence-released USD escrow is MVP 0.2, full MSCFA is
  MVP 0.3. Never call them V1 / V2.
- Before each implementation, map the code with Graphify: `graphify update .`, then `graphify explain "<X>"` /
  `graphify path "<A>" "<B>"`. Output in `graphify-out/` (git-ignored).
- Coding rules: [`CODING_STANDARDS.md`](CODING_STANDARDS.md). They override generic advice from any skill.
- Code review and refactor: follow [`docs/process/review-and-refactor.md`](docs/process/review-and-refactor.md),
  which gives each installed skill (`.claude/skills/`) its role: `code-review` leads, `security-review`,
  `golang-design-patterns`, `python-design-patterns` and `devops-engineer` advise in their scope, `refactor` is
  the only one that changes code.
- Tests: the touched service's `pytest -q` (each service has its own `.venv`) or `go test ./...` in `chain/`;
  `scripts/test-all.sh` for cross-service changes.
