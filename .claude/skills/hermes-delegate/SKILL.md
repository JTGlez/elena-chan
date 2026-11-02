---
name: hermes-delegate
description: Use when coding work should be handed to the Hermes agent while Claude supervises.
---

# Delegate coding to Hermes

Hermes writes the code. The supervisor writes briefs, verifies the results and sends corrections. When this skill is active, the supervisor edits no code in the delegated scope, including small fixes: a defect becomes a correction.

`scripts/delegate.py` is in this skill's folder. Run it with `python3`; `--help` lists every option.

## Before the first turn

Add `.hermes-delegate.json` at the project root. `start` refuses to run without `provider` and `model`, so work never falls through to Hermes' default model:

```json
{
  "provider": "<provider name in Hermes>",
  "model": "<model id>",
  "busy_check": {
    "command": ["<program>", "<args>"],
    "expected_active": "<optional backend name>"
  },
  "guard": {
    "blocked_commands": [{"pattern": "^deploy\\b", "reason": "deploys are the user's"}],
    "blocked_code": [{"pattern": "\\bdeploy\\(", "reason": "deploys are the user's"}],
    "secret_paths": ["(^|/)\\.env$"],
    "protected_paths": ["(^|/)generated/"]
  }
}
```

Only `provider` and `model` are required.

- **`busy_check`:** for a backend that serves one stream at a time. The command runs from the project root and prints JSON with `inflight` and optionally `active`. A turn will not start while `inflight` is above zero, or while another backend is active.
- **`blocked_commands`:** regular expressions matched against each simple command, written as its program name followed by its arguments.
- **`blocked_code`:** regular expressions matched against `execute_code` source.
- **`secret_paths`:** blocked for reading and writing. **`protected_paths`:** blocked for writing.

Then:

1. Confirm no interactive Hermes session is working in the same project. `delegate.py` only knows its own turns, and two writers corrupt each other's work. If one is active, ask the user to close it.
2. Run `install-guard` once. It adds a hook to Hermes' config. In any project with a `.hermes-delegate.json`, the hook blocks your rules plus `git push`, `--no-verify`, `git reset --hard`, `git clean -f` and `git add -A`. Other projects are unaffected. Undo with `uninstall-guard`.
3. In a throwaway turn, ask the agent to run `git push --dry-run no-such-remote` and look for `Blocked by project guard`. Repeat after updating Hermes.

The guard stops honest mistakes. It is not a sandbox.

## The loop

1. **Write one brief per task**, in a file. Use a new label, and so a new session, for each task.
2. **Start it:** `python3 scripts/delegate.py start <label> --brief <file> --cwd <project>`.
3. **Follow it:** run `python3 scripts/delegate.py watch <label>` under the Monitor tool with `timeout_ms: 1800000`. The turn keeps running if the watcher expires; start `watch` again and it continues where it stopped.
4. **Verify** when `RESULT` appears, using the gate below. The agent's report is a claim to check.
5. **Correct:** `python3 scripts/delegate.py send <label> --brief <file>`, then go back to step 3. A correction needs no required sections.
6. **Close** when the gate passes: record the numbers from `status <label>` and take the next task.

To cut a wrong turn short, run `stop <label>`, then `send` the correction. The interruption lands at the next safe point.

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

Spend your effort on the edge cases: they are what an implementing agent is most likely to miss. Give facts you already found, such as file and line, so the agent does not spend turns rediscovering them.

Keep briefs to the task. Evaluations of the agent and notes for the user stay out of them.

## Verification gate

Check all of these in a copy made with `git archive <sha> | tar -x -C <scratch dir>`. Do not run them in the agent's working tree while a turn is live.

1. `git log` and `git diff --stat <base>..<sha>` show only the files the brief allowed.
2. The brief's commands pass, run by you.
3. Each edge case is exercised by a probe of your own, written outside the repository.
4. Mutants: break each guard and branch in the copy, and confirm a test fails. A survivor becomes a correction.
5. Plan-level requirements hold: read the plan's task text again, not your memory of it.

## Limits

- One turn runs at a time, and subagents the agent spawns share its backend.
- `status` reports what happened, not whether it is right. Only the gate says that.
- Pushing, merging and deploying stay with the user.
- After three corrections on the same defect with no progress, stop and tell the user what failed. Do not take over the code silently.

## Common mistakes

| Mistake | Result |
|---|---|
| Running `hermes -z` by hand | Dangerous commands are approved automatically |
| One session for several tasks | Context compaction costs minutes and many tokens |
| Treating `SILENT` as a hang | Long thinking is silent too; read the model provider's logs first |
