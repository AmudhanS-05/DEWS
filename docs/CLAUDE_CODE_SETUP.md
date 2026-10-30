# Running the three workstreams in Claude Code

Why: Claude Code runs on the laptop next to Docker, Kafka and PostgreSQL, so each session can run the pipeline and
the tests itself and loop until `verify_slice` passes, instead of you pasting outputs. Three sessions run in parallel,
each in its own git worktree (a separate folder and branch of the same repo). Paper and slides stay in claude.ai chats.

## One-time setup (about 20 minutes)
1. **Install Claude Code** from Anthropic's documentation (docs.claude.com → Claude Code) and sign in. Check that
   your plan includes it.
2. **Move the repo out of a OneDrive-synced folder** if `Documents` is synced (that is the source of the stray git
   lock files). For example `C:\dev\dews\disaster-warn-kafka` and the venv `C:\dev\dews\spaenv` (recreate the venv:
   `python -m venv C:\dev\dews\spaenv`, then `pip install -r requirements-dev.txt`).
3. **Create the worktrees** (main folder stays on `master` and is the core workstream):
   ```powershell
   cd C:\dev\dews\disaster-warn-kafka
   git worktree add ..\dews-ml -b ml
   git worktree add ..\dews-agents -b agents
   ```
4. **Share the git-ignored local files** with the worktrees (data, secrets, gauge cache):
   ```powershell
   cmd /c mklink /J C:\dev\dews\dews-ml\data C:\dev\dews\disaster-warn-kafka\data
   cmd /c mklink /J C:\dev\dews\dews-ml\gate_output C:\dev\dews\disaster-warn-kafka\gate_output
   copy .env C:\dev\dews\dews-agents\.env
   ```
   `BREADCRUMBS.md` stays only in the main folder; every session appends to that one file.
5. **Slash commands:** copy `docs/claude-commands/*.md` into `.claude/commands/` in each of the three folders
   (`mkdir .claude\commands; copy docs\claude-commands\*.md .claude\commands\`). This gives `/slice-status` and
   `/verify-slice S1` in every session.
6. **One Docker stack for everyone:** `cd docker; docker compose up -d` in the main folder. Only the core session
   starts Faust workers, the console and the demo scripts.

## Daily loop
1. Open three terminals: `C:\dev\dews\disaster-warn-kafka`, `C:\dev\dews\dews-ml`, `C:\dev\dews\dews-agents`.
2. Start `claude` in each and paste the starter prompt from `docs/handoffs/CORE.md`, `ML_INDIA.md` or `AGENTS.md`
   (next slice: replace S1 with S2 and so on).
3. Let them run; answer only the **YOU** items. Useful commands in any session: `/slice-status` (what is left in the
   current slice) and `/verify-slice` (runs the acceptance checks).
4. **End of a slice** (core session): "Merge ml and agents, run pytest and verify_slice S<n>, add the scene to
   docs/DEMO.md." Then the other two sessions pull master into their branches:
   `git merge master` (in their own folders).
5. Read the breadcrumbs and `docs/DEMO.md`; run the demo scene yourself once.

## Rules that keep parallel sessions out of each other's way
Folder ownership (`docs/BUILD_PHASES.md`, "Who works where"); interfaces only through `docs/CONTRACTS.md`; one console
(port 8765) and one set of Faust workers (6066 to 6068), owned by core; tests for agents run without network
(`LLM_PROVIDER=none`); ML runs offline on cached data.
