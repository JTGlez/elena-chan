# Supervising Hermes from Codex

Delegation exists to save the supervisor's tokens. Every model call resends the whole context, so the number of calls made while waiting is the main cost.

## Wait for a turn

Run this as one `exec` call, changing only `<label>`. It costs one model call per cycle, and it returns as soon as the turn ends:

```js
// @exec: {"yield_time_ms": 295000, "max_output_tokens": 2000}
const delegate = ".agents/skills/hermes-delegate/scripts/delegate.py";
const watch = await tools.exec_command({
  cmd: `python3 ${delegate} watch <label> --timeout 280 --digest`,
  login: false, sandbox_permissions: "require_escalated", yield_time_ms: 1000, max_output_tokens: 2000,
});
text(watch.output);
if (watch.session_id) {
  const rest = await tools.write_stdin({session_id: watch.session_id, yield_time_ms: 285000, max_output_tokens: 2000});
  text(rest.output);
  text(`watch exit: ${rest.exit_code}`);
} else {
  text(`watch exit: ${watch.exit_code}`);
}
```

These three values are what make the wait cheap. A run that dropped them made 14 model calls in 5 minutes.

- **Keep the `// @exec` first line.** Without it, `exec` yields after 30 s and each cycle costs a second model call. If `exec` still returns a cell ID, call `wait` with `yield_time_ms: 295000`.
- **Keep `--timeout 280`, however short the turn looks.** `watch` returns as soon as the turn ends, so a shorter timeout brings the result no sooner and only adds model calls.
- **Keep `write_stdin` with `yield_time_ms: 285000`, inside the same `exec`.** A run that kept `--timeout 280` but polled `write_stdin` in its own 50 s calls made 9 model calls in 4 minutes.

| `watch exit` | Meaning | Next call |
|---|---|---|
| 3 | Still running. `RUNNING` shows elapsed time, calls, files written and the age of the last event. | The same `exec`, with no other tool call and no message to the user. |
| 0 | `RESULT` with exit 0. | `report <label>`, then the verification gate. |
| 1 | `RESULT` with a failure, or `DIED`. | `status <label>`. If it says `detached`, run `stop <label>`. |

If a session has no `exec` tool, make the same two calls directly.

## What to read

- **The agent's full report:** `report <label>`. `RESULT` shows only the first 3000 characters.
- **Progress for the user:** the last `RUNNING` line. Run `status <label>` only when the user asks for more; it adds real API calls and tokens under `session`.
- **Long silence:** `last_event_age_s` keeps growing while the model generates one large file, because the tool call is reported only when it is complete. If `status` shows `activity` and the gate shows `inflight: 1`, the model is working.
- **The delivery:** `snapshot <label> --base <rev>`, once the turn has ended. Its fingerprint identifies a delivery with no commit.

Facts for the next brief go in the brief, so read them before `start`. Read each file once, in the range you need: AGENTS.md is already in your context.

## Sandbox and approvals

- Run every `delegate.py` command with `sandbox_permissions: "require_escalated"`. Commands write to `~/.local/state/hermes-delegate`, reach the Hermes gateway and need to see host processes.
- The sandbox hides host processes, so `pgrep` finds no Hermes there, and it kills a turn when the command ends. `delegate.py` refuses to run inside it.
- Inside the sandbox, `model_gate.py doctor` reports a missing `nvidia-smi` and `make test-tools` fails on local sockets. Run them escalated before believing either result.
- Ask for one `prefix_rule` per subcommand, such as `[..., "delegate.py", "watch"]`. A rule ending at `delegate.py` also approves `uninstall-guard`.
- Call scripts through one path, `.agents/skills/...`. A rule for `.claude/skills/...` does not match it.

## Changing what a turn depends on

Before another session reloads the model gate, swaps models or edits `~/.hermes/config.yaml`, run `sessions`. If it lists a client or a session with `running` not `false`, wait for the turn to end.
