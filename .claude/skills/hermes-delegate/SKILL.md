---
name: hermes-delegate
description: Use when coding work should be handed to the Hermes agent while an orchestrating agent, such as Claude Code or Codex, supervises.
---

# Delegate coding to Hermes

Hermes writes the code. The orchestrating agent, called the supervisor below, writes briefs, verifies the results and sends corrections. When this skill is active, the supervisor edits no code in the delegated scope, including small fixes: a defect becomes a correction.

`scripts/delegate.py` is in this skill's folder. Run it with `python3`; `--help` lists every option.

## Before the first turn

A project has two config files, read from its root:

- `.hermes-delegate.json` is shared and versioned: the project's guard rules.
- `.hermes-delegate.local.json` is this machine's and stays out of version control: which Hermes route to use. Add it to `.gitignore`.

Local values override shared ones, and guard rules from both apply. Hermes owns the choice of model. This skill passes these values through without interpreting them:

```json
{
  "provider": "<Hermes provider>",
  "model": "<model id>",
  "ready_command": ["<program>", "<args>"]
}
```

```json
{
  "guard": {
    "blocked_commands": [{"pattern": "^deploy\\b", "reason": "deploys are the user's"}],
    "blocked_code": [{"pattern": "\\bdeploy\\(", "reason": "deploys are the user's"}],
    "secret_paths": ["(^|/)\\.env$"],
    "protected_paths": ["(^|/)generated/"]
  }
}
```

- **Route:** set `provider` and `model` together, or `"use_hermes_default": true` to run whatever Hermes is configured with. `start` refuses a project that chose neither, so work never reaches a default by accident.
- **`ready_command`:** optional. Runs from the project root before each turn. Exit 0 means go; any other exit stops the turn and shows its output.
- **`blocked_commands`:** regular expressions matched against each simple command, written as its program name followed by its arguments.
- **`blocked_code`:** regular expressions matched against `execute_code` source.
- **`secret_paths`:** blocked for reading and writing. **`protected_paths`:** blocked for writing.

Then:

1. Run `install-guard` once, and again when `start` asks for it after an update. It adds a hook to Hermes' config. In any project with one of these files, the hook blocks your rules plus `git push`, `--no-verify`, `git reset --hard`, `git clean -f` and `git add -A`. Other projects are unaffected. Undo with `uninstall-guard`.
2. In a throwaway turn, ask the agent to run `git push --dry-run no-such-remote` and look for `Blocked by project guard`. Repeat after updating Hermes.

The guard stops honest mistakes. It is not a sandbox.

## The loop

1. **Write one brief per task**, in a file. Use a new label, and so a new session, for each task.
2. **Start it:** `python3 scripts/delegate.py start <label> --brief <file> --cwd <project>`.
3. **Follow it:** run `python3 scripts/delegate.py watch <label>` so it does not block your session. It returns by `--timeout` (exit 3 if the turn still runs), and the turn keeps running after it returns; run `watch` again and it continues where it stopped. Waiting costs tokens, so follow the guide for your orchestrator:
   - **Codex:** read `orchestrators/codex.md` before the first turn.
   - **Claude Code:** use the Monitor tool with `timeout_ms: 1800000`.
4. **Verify** when `RESULT` appears, using the gate below. The agent's report is a claim to check.
5. **Correct:** `python3 scripts/delegate.py send <label> --brief <file>`, then go back to step 3. A correction needs no required sections.
6. **Close** when the gate passes: record the numbers from `status <label>` and take the next task.

To cut a wrong turn short, run `stop <label>`, then `send` the correction. The interruption lands at the next safe point, and `stop` prints `stopped` only once the gateway confirms it.

## One Hermes session at a time

Killing the `hermes chat` client only detaches it: the session keeps working at the gateway. So:

- `start` and `send` refuse while any Hermes session is at work, delegated or not, and simultaneous calls launch only one.
- `status` shows a run the client left behind as `detached`. Run `stop <label>` on it; killing processes by hand can take the whole gateway down.
- `sessions` lists the clients, watchers and sessions alive right now. Run it when something seems stuck, and before another session changes the model or Hermes' config.
- A delegated turn cannot start subagents: the guard blocks `delegate_task` in sessions `delegate.py` started.

## Brief

`start` refuses a brief that lacks one of these sections:

```markdown
## Goal
One outcome, and why it matters. Link the plan task and the spec section.
## Files
Paths it may create or change. Everything else is out of scope.
## Tests
Named tests, and the exact edge cases: event orders, boundaries, error paths.
Each one states the production change that would make it fail.
## Verify
Exact commands and the expected output. Say to watch the test fail first.
## Forbidden
What it must not do, beyond what the guard blocks.
## Report
Commit SHA, the commands it ran with their real output, deviations, open doubts.
```

When an order matters, such as seeing a test fail before production code exists, end the turn there and `send` the next step. An agent can drift from an order written in a brief, and a turn that ends reaches you without polling.

Spend your effort on the edge cases: they are what an implementing agent is most likely to miss. Give facts you already found, such as file and line, so the agent does not spend turns rediscovering them.

Keep briefs to the task. Evaluations of the agent and notes for the user stay out of them.

## Verification gate

Check all of these in a copy made with `git archive <sha> | tar -x -C <scratch dir>`, or with `snapshot <label> --base <rev>` for a delivery with no commit. Do not run them in the agent's working tree while a turn is live.

1. `git log` and `git diff --stat <base>..<sha>` show only the files the brief allowed.
2. The brief's commands pass, run by you.
3. Each edge case is exercised by a probe of your own, written outside the repository.
4. Mutants: break each guard and branch in the copy, and confirm a test fails. A survivor becomes a correction.
5. Plan-level requirements hold: read the plan's task text again, not your memory of it.

## Limits

- One turn runs at a time, across all projects on this machine.
- `status` reports what happened, not whether it is right. Only the gate says that.
- A call the guard blocks emits no event, so `watch` does not show it. Require blocked calls in the brief's report.
- Pushing, merging and deploying stay with the user.
- After three corrections on the same defect with no progress, stop and tell the user what failed. Do not take over the code silently.

## Common mistakes

| Mistake | Result |
|---|---|
| Running `hermes -z` by hand | Dangerous commands are approved automatically |
| One session for several tasks | Context compaction costs minutes and many tokens |
| Treating `SILENT` as a hang | Long thinking is silent too; check where the model runs before acting |
| Trusting `RESULT exit=130` as a stop | It only says the client ended; check `status` for `detached` |
