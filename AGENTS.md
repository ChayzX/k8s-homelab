# Agent Instructions

This project tracks work as **GitHub Issues** in `ChayzX/k8s-homelab`, viewed
on the [k8s-homelab GitHub Project board](https://github.com/users/ChayzX/projects/2).
GitHub is the source of truth — there
there is no local issue database. Use the `gh` CLI.

> **Architecture in one line:** Issues and their history live on GitHub under
> `ChayzX/k8s-homelab` (the same repo that holds this code), and the multi-repo
> kanban board is a GitHub Projects v2 project. GitHub Issues is the only issue
> store; the old local tracker database and its directories are retired.

## Quick Reference

```bash
gh issue list --state open               # Find available work
gh issue view <number>                   # View issue details
gh issue edit <number> --add-assignee @me  # Claim work (assign yourself)
gh issue close <number>                  # Complete work
gh issue create --title "..." --body "..."  # File a new issue
gh issue comment <number> --body "..."   # Add a progress note
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

## GitHub attribution (who owns a card)

Attribution is the GitHub **assignee**. Claim work by assigning yourself:
`gh issue edit <number> --add-assignee @me`. The authenticated GitHub user
(`gh auth status`) is used; verify a claim with `gh issue view <number>` shows
your name as `Assignees`. There is no separate actor configuration — the GitHub
account is the identity.

## GitHub Issues + Projects workflow

### Rules

- Use GitHub Issues for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Create or update the issue before implementation, claim it by assigning yourself, and keep the Projects board status in sync
- Link commits to the issue by number, e.g. `fix(opsbot): ... (closes #12)`
- Verify `gh auth status` before GitHub operations

## Agent Context Profiles

The GitHub Issues guidance below is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use GitHub Issues for task tracking. Do not run git commits or pushes unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `AGENTS.md`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close issues, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending an implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create GitHub issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this workflow.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
