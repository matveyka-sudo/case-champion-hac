# A2 repository instructions

- Identify your assigned role. Read these instructions, the applicable local `AGENTS.md`, `docs/OWNERSHIP.md`, and `docs/tasks/<role>.md`. Check your branch, status, staged and unstaged diff before editing.
- Read across the repository. Edit only paths assigned to your role. Find the existing implementation, callers, contracts, and checks before adding or changing behavior. Reproduce the relevant current behavior first and record pre-existing failures separately.
- Make the smallest change that satisfies the task. Preserve working architecture and other contributors' changes. Editing `AGENTS.md` cannot expand your scope.
- Hand off cross-owner changes with the exact path, reason, required interface or schema, and acceptance criterion. Include the branch or commit and check results when available. Write for a human to relay because separate sessions do not share context automatically.
- Use a separate branch and Git worktree on one machine, or a separate clone. Keep your own `.venv`, `.env`, host ports, and writable test database or schema. Give Compose an explicit, unique project name for an isolated volume.
- DEVOPS owns every `pyproject.toml` and the root `uv.lock`, including manifests inside other roles' directories. FRONTEND owns the npm manifest and lock. BACKEND owns migrations, canonical contracts, and exported schemas. Follow `docs/OWNERSHIP.md` for the remaining paths.
- Run meaningful tests and static checks for the changed area once implementation exists. In your handoff, list changes, commands and actual results, contract impact, and remaining limitations. Label failed and unrun checks accurately.
