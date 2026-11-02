#!/usr/bin/env python3
"""Run bounded coding turns on the local Hermes agent and follow them from another agent.

A turn runs detached, so a watcher that expires never stops the work. Standard library only.
Run with --help for the commands.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "guard_hook.py"
CONFIG_NAME = ".hermes-delegate.json"
REQUIRED_SECTIONS = ("Goal", "Files", "Tests", "Verify", "Forbidden", "Report")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
GUARD_BEGIN = "# >>> hermes-delegate guard >>>"
GUARD_END = "# <<< hermes-delegate guard <<<"
RESULT_PREVIEW = 3000
INPUT_PREVIEW = 200


class DelegateError(Exception):
    pass


def hermes_binary():
    return os.environ.get("HERMES_DELEGATE_HERMES") or shutil.which("hermes") or "hermes"


def state_root():
    default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "hermes-delegate"
    return Path(os.environ.get("HERMES_DELEGATE_STATE", default))


def config_path():
    return Path(os.environ.get("HERMES_DELEGATE_CONFIG", Path.home() / ".hermes/config.yaml"))


def find_project(cwd):
    """The nearest directory holding a project config, and that config."""
    for directory in (cwd, *cwd.parents):
        path = directory / CONFIG_NAME
        if path.is_file():
            config = json.loads(path.read_text())
            missing = [key for key in ("provider", "model") if not config.get(key)]
            if missing:
                raise DelegateError(f"{path} lacks: {', '.join(missing)}")
            return directory, config
    raise DelegateError(f"no {CONFIG_NAME} at or above {cwd}: see the skill's setup section")


def run_dir(label):
    if not LABEL.fullmatch(label):
        raise DelegateError(f"invalid label: {label!r}")
    return state_root() / label


def load_state(label):
    path = run_dir(label) / "state.json"
    if not path.exists():
        raise DelegateError(f"no run named {label!r}")
    return json.loads(path.read_text())


def save_state(label, state):
    directory = run_dir(label)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "state.json.tmp"
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(directory / "state.json")


def process_alive(pid):
    if not pid:
        return False
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return False
    return fields[0] != "Z"  # a zombie has finished: only its exit status is left


def read_events(path):
    events = []
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def turn_log(label, turn):
    return run_dir(label) / f"turn-{turn}.jsonl"


def live_runs():
    root = state_root()
    if not root.exists():
        return []
    labels = []
    for state_file in root.glob("*/state.json"):
        state = json.loads(state_file.read_text())
        if process_alive(state.get("pid")):
            labels.append(state_file.parent.name)
    return labels


def guard_installed():
    path = config_path()
    return path.exists() and GUARD_BEGIN in path.read_text() and str(GUARD) in path.read_text()


def check_busy(root, config):
    busy = config.get("busy_check")
    if not busy:
        return
    result = subprocess.run(busy["command"], capture_output=True, text=True, timeout=30, cwd=root)
    if result.returncode != 0:
        raise DelegateError(f"busy_check failed: {result.stderr.strip()[:300]}")
    status = json.loads(result.stdout)
    if status.get("inflight"):
        raise DelegateError(f"the backend is busy: inflight={status['inflight']}; wait for it")
    active, expected = status.get("active"), busy.get("expected_active")
    if active and expected and active != expected:
        raise DelegateError(f"active backend is {active}, not {expected}; switching it is the user's call")


def check_brief(path):
    text = path.read_text()
    missing = [name for name in REQUIRED_SECTIONS
               if not re.search(rf"^#+\s*{name}\b", text, re.M | re.I)]
    if missing:
        raise DelegateError(f"brief lacks required sections: {', '.join(missing)}")


def launch(label, state, brief, args):
    running = live_runs()
    if running:
        raise DelegateError(f"a turn is already running: {', '.join(running)}")
    if not guard_installed():
        raise DelegateError(f"the guard is not in {config_path()}: run install-guard first")
    root, config = find_project(Path(state["cwd"]))
    check_busy(root, config)

    directory = run_dir(label)
    directory.mkdir(parents=True, exist_ok=True)
    turn = state.get("turn", 0) + 1
    brief_copy = directory / f"brief-{turn}.md"
    shutil.copyfile(brief, brief_copy)

    command = [hermes_binary(), "chat", "--query-file", str(brief_copy), "-Q", "--format", "stream-json",
               "--provider", config["provider"], "-m", config["model"], "--in", state["cwd"], "--source", "tool", "--accept-hooks",
               "--max-turns", str(args.max_turns), "--run-budget", str(args.run_budget)]
    if args.skills:
        command += ["--skills", args.skills]
    if state.get("session_id"):
        command += ["--resume", state["session_id"]]

    with turn_log(label, turn).open("wb") as stdout, (directory / f"turn-{turn}.stderr").open("wb") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=True, close_fds=True, cwd=state["cwd"])
    state.update(turn=turn, pid=process.pid, watch_offset=0, watch_turn=turn, started_at=time.time(),
                 command=shlex.join(command))
    save_state(label, state)
    print(f"{label}: turn {turn} started, pid {process.pid}")
    print(f"follow it: python3 {Path(__file__).resolve()} watch {label}")
    return 0


def cmd_start(args):
    brief = Path(args.brief)
    check_brief(brief)
    directory = run_dir(args.label)
    if (directory / "state.json").exists():
        raise DelegateError(f"run {args.label!r} exists: use send to continue it, or pick a new label")
    return launch(args.label, {"label": args.label, "cwd": str(Path(args.cwd).resolve())}, brief, args)


def cmd_send(args):
    state = load_state(args.label)
    summarize_session(args.label, state)
    if not state.get("session_id"):
        raise DelegateError("the last turn recorded no session_id: nothing to resume")
    return launch(args.label, state, Path(args.brief), args)


def summarize_session(label, state):
    for event in read_events(turn_log(label, state.get("turn", 0))):
        if event.get("session_id"):
            state["session_id"] = event["session_id"]


def shorten(value, limit):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def tool_input_preview(event):
    data = event.get("input")
    if isinstance(data, dict):
        for key in ("command", "path", "pattern", "goal"):
            if data.get(key):
                return shorten(data[key], INPUT_PREVIEW)
    return shorten(data or "", INPUT_PREVIEW)


def print_event(event):
    kind = event.get("type")
    if kind == "tool_use":
        print(f"TOOL {event.get('name')}: {tool_input_preview(event)}", flush=True)
    elif kind == "tool_result" and (event.get("is_error") or "BLOCKED" in str(event.get("output", ""))
                                    or "Blocked by project guard" in str(event.get("output", ""))):
        print(f"ERROR {event.get('name')}: {shorten(event.get('output', ''), 400)}", flush=True)
    elif kind == "result":
        tokens = event.get("tokens") or {}
        error = f" error={shorten(event['error'], 300)}" if event.get("error") else ""
        print(f"RESULT exit={event.get('exit_code')} output_tokens={tokens.get('output')} "
              f"duration_s={round((event.get('duration_ms') or 0) / 1000)}{error}", flush=True)
        print(shorten(event.get("text") or "", RESULT_PREVIEW), flush=True)
        return True
    return False


def cmd_watch(args):
    state = load_state(args.label)
    turn = state["turn"]
    path = turn_log(args.label, turn)
    offset = state.get("watch_offset", 0) if state.get("watch_turn") == turn else 0
    buffered = b""
    last_seen, flagged = time.time(), False

    def persist():
        fresh = load_state(args.label)
        fresh.update(watch_offset=offset, watch_turn=turn)
        summarize_session(args.label, fresh)
        save_state(args.label, fresh)

    def result_event():
        for event in reversed(read_events(path)):
            if event.get("type") == "result":
                return event
        return None

    while True:
        alive = process_alive(state.get("pid"))
        progressed = False
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
        if chunk:
            buffered += chunk
            lines = buffered.split(b"\n")
            buffered = lines.pop()
            offset += len(chunk)
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                progressed = True
                if print_event(event):
                    persist()
                    return 0 if event.get("exit_code") == 0 else 1
        if progressed:
            last_seen, flagged = time.time(), False
        if not alive and not chunk:
            persist()
            final = result_event()
            if final is not None:  # already shown by an earlier watch
                print_event(final)
                return 0 if final.get("exit_code") == 0 else 1
            stderr = run_dir(args.label) / f"turn-{turn}.stderr"
            tail = stderr.read_text(errors="replace")[-400:] if stderr.exists() else ""
            print(f"DIED: the process ended with no result record. {shorten(tail, 400)}", flush=True)
            return 1
        if not flagged and time.time() - last_seen > args.quiet_after:
            flagged = True
            print(f"SILENT: no event for {args.quiet_after} s. Long thinking also looks like this; "
                  "check the worker's log before acting.", flush=True)
        persist()
        time.sleep(args.poll)


def cmd_stop(args):
    state = load_state(args.label)
    pid = state.get("pid")
    if not process_alive(pid):
        print(f"{args.label}: not running")
        return 0
    os.killpg(pid, signal.SIGINT)
    deadline = time.time() + args.wait
    while time.time() < deadline and process_alive(pid):
        time.sleep(0.2)
    if process_alive(pid):
        print(f"{args.label}: still stopping after {args.wait} s, pid {pid}")
        return 1
    print(f"{args.label}: stopped")
    return 0


def run_summary(label):
    state = load_state(label)
    summarize_session(label, state)
    turns, total_output, total_input = [], 0, 0
    final_result = None
    for number in range(1, state["turn"] + 1):
        events = read_events(turn_log(label, number))
        result = next((event for event in reversed(events) if event.get("type") == "result"), None)
        tokens = (result or {}).get("tokens") or {}
        total_output += tokens.get("output") or 0
        total_input += tokens.get("input") or 0
        turns.append({
            "turn": number,
            "exit_code": (result or {}).get("exit_code"),
            "duration_s": round(((result or {}).get("duration_ms") or 0) / 1000),
            "input_tokens": tokens.get("input"),
            "output_tokens": tokens.get("output"),
            "tool_calls": sum(event.get("type") == "tool_use" for event in events),
            "tool_errors": sum(event.get("type") == "tool_result" and bool(event.get("is_error")) for event in events),
        })
        final_result = result
    if process_alive(state.get("pid")):
        status = "running"
    elif final_result is not None:
        status = "finished"
    else:
        status = "crashed"
    return {"label": label, "state": status, "session_id": state.get("session_id"), "cwd": state.get("cwd"),
            "total_input_tokens": total_input, "total_output_tokens": total_output, "turns": turns}


def cmd_status(args):
    if args.label:
        print(json.dumps(run_summary(args.label), indent=2))
        return 0
    root = state_root()
    labels = sorted(path.parent.name for path in root.glob("*/state.json")) if root.exists() else []
    print(json.dumps([{key: summary[key] for key in ("label", "state", "total_output_tokens")}
                      for summary in map(run_summary, labels)], indent=2))
    return 0


def cmd_install_guard(_args):
    path = config_path()
    text = path.read_text()
    if GUARD_BEGIN in text:
        print(f"guard already in {path}")
        return 0
    if re.search(r"^hooks:", text, re.M):
        raise DelegateError(f"{path} already has a hooks: block; merge this by hand: {GUARD}")
    backup = Path(str(path) + ".bak-before-delegate")
    if not backup.exists():
        shutil.copy2(path, backup)
    block = (f"\n{GUARD_BEGIN}\nhooks:\n  pre_tool_call:\n"
             '    - matcher: "terminal|execute_code|read_file|write_file|patch"\n'
             f'      command: "{shlex.quote(sys.executable)} {shlex.quote(str(GUARD))}"\n'
             f"      timeout: 10\n      fail_closed: true\n{GUARD_END}\n")
    path.write_text(text + block)
    print(f"guard added to {path}; backup at {backup}")
    return 0


def cmd_uninstall_guard(_args):
    path = config_path()
    text = path.read_text()
    updated = re.sub(rf"\n?{re.escape(GUARD_BEGIN)}.*?{re.escape(GUARD_END)}\n", "", text, flags=re.S)
    path.write_text(updated)
    print(f"guard removed from {path}" if updated != text else f"no guard in {path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def turn_options(sub):
        sub.add_argument("label")
        sub.add_argument("--brief", required=True, help="markdown file holding the instruction")
        sub.add_argument("--max-turns", type=int, default=80, help="tool-call iterations for this turn")
        sub.add_argument("--run-budget", type=int, default=2700, help="wall-clock seconds for this turn")
        sub.add_argument("--skills", help="Hermes skills to preload, comma separated")

    start = commands.add_parser("start", help="start a new session from a full brief")
    turn_options(start)
    start.add_argument("--cwd", default=".", help="directory the agent works in")
    start.set_defaults(func=cmd_start)

    send = commands.add_parser("send", help="send a correction to the same session")
    turn_options(send)
    send.set_defaults(func=cmd_send)

    watch = commands.add_parser("watch", help="print new events; exits when the turn ends")
    watch.add_argument("label")
    watch.add_argument("--quiet-after", type=int, default=600, help="flag this many seconds of silence")
    watch.add_argument("--poll", type=float, default=0.5)
    watch.set_defaults(func=cmd_watch)

    stop = commands.add_parser("stop", help="interrupt the running turn (SIGINT)")
    stop.add_argument("label")
    stop.add_argument("--wait", type=int, default=60)
    stop.set_defaults(func=cmd_stop)

    status = commands.add_parser("status", help="state, tokens and tool counts as JSON")
    status.add_argument("label", nargs="?")
    status.set_defaults(func=cmd_status)

    commands.add_parser("install-guard", help="add the project guard to Hermes' config").set_defaults(
        func=cmd_install_guard)
    commands.add_parser("uninstall-guard", help="remove the project guard").set_defaults(func=cmd_uninstall_guard)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DelegateError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
