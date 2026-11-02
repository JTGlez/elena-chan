#!/usr/bin/env python3
"""Run bounded coding turns on the local Hermes agent and follow them from another agent.

A turn runs detached, so a watcher that expires never stops the work. Standard library only.
Run with --help for the commands.
"""
import argparse
import collections
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "guard_hook.py"
SESSION_HELPER = Path(__file__).resolve().parent / "hermes_session.py"
CONFIG_NAMES = (".hermes-delegate.json", ".hermes-delegate.local.json")  # shared, then this machine's
REQUIRED_SECTIONS = ("Goal", "Files", "Tests", "Verify", "Forbidden", "Report")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
GUARD_BEGIN = "# >>> hermes-delegate guard >>>"
GUARD_END = "# <<< hermes-delegate guard <<<"
GUARD_MATCHER = "terminal|execute_code|read_file|write_file|patch|delegate_task"
RESULT_PREVIEW = 3000
INPUT_PREVIEW = 200
DEFAULT_MAX_TURNS = 80
DEFAULT_WATCH_TIMEOUT = 1800
EXIT_STILL_RUNNING = 3
HELPER_TIMEOUT = 30
CLIENT_GRACE = 2
DIGEST_PATHS = 8
SESSION_LINE = re.compile(r"^Session: (\S+)$", re.M)
PID1_COMMAND = Path("/proc/1/cmdline")
SANDBOX_INITS = ("codex-linux-sandbox", "bwrap")


class DelegateError(Exception):
    pass


def check_not_sandboxed():
    """In a sandbox with its own PID namespace a turn dies when the command ends and host processes are hidden."""
    try:
        program = PID1_COMMAND.read_bytes().split(b"\0")[0].decode(errors="replace")
    except OSError:
        return
    if Path(program).name in SANDBOX_INITS:
        raise DelegateError("this sandbox hides host processes and kills a turn when the command ends: run delegate.py "
                            'outside it (in Codex, with sandbox_permissions: "require_escalated")')


def hermes_binary():
    return os.environ.get("HERMES_DELEGATE_HERMES") or shutil.which("hermes") or "hermes"


def state_root():
    default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "hermes-delegate"
    return Path(os.environ.get("HERMES_DELEGATE_STATE", default))


def config_path():
    return Path(os.environ.get("HERMES_DELEGATE_CONFIG", Path.home() / ".hermes/config.yaml"))


def find_project(cwd):
    """The nearest directory with a project config, and its shared and machine settings merged."""
    for directory in (cwd, *cwd.parents):
        paths = [directory / name for name in CONFIG_NAMES]
        if any(path.is_file() for path in paths):
            config = {}
            for path in paths:
                if path.is_file():
                    config.update(json.loads(path.read_text()))
            check_route(directory, config)
            return directory, config
    raise DelegateError(f"no {CONFIG_NAMES[0]} or {CONFIG_NAMES[1]} at or above {cwd}: see the skill's setup section")


def check_route(directory, config):
    """Which model runs is Hermes' business, but the choice must be explicit: never an accidental default."""
    if bool(config.get("provider")) != bool(config.get("model")):
        raise DelegateError(f"{directory}: set provider and model together")
    if not config.get("provider") and not config.get("use_hermes_default"):
        raise DelegateError(f"{directory}: no route. Set provider and model, or use_hermes_default: true, "
                            f"in {CONFIG_NAMES[1]}")


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


def process_start(pid):
    """The kernel's start time for a live process, or None. A zombie has finished: only its exit status is left."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return None
    return None if fields[0] == "Z" else fields[19]


def client_alive(state):
    """Whether this run's own `hermes chat` client still runs. A recycled pid has another start time."""
    pid = state.get("pid")
    if not pid:
        return False
    started = process_start(pid)
    return started is not None and state.get("pid_start") in (None, started)


def session_row(session_id):
    """The session's row in Hermes' store, read-only. Empty when the store cannot say."""
    if not session_id:
        return {}
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    try:
        db = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True, timeout=2)
        try:
            db.row_factory = sqlite3.Row
            row = db.execute("select * from sessions where id = ?", (session_id,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return {}
    return dict(row) if row else {}


def gateway_running(action, session_id):
    """Run the session helper in Hermes' runtime. True or False from the gateway; None when it cannot tell."""
    # cProfile is the standard module that runs a script by path; --run-module takes only module names.
    command = [hermes_binary(), "--run-module", "cProfile", "-o", os.devnull, str(SESSION_HELPER), action, session_id]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=HELPER_TIMEOUT,
                                stdin=subprocess.DEVNULL)
        return bool(json.loads(result.stdout.strip().splitlines()[-1])["running"]) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, KeyError, TypeError):
        return None


def run_is_live(label, state):
    """The client runs, or the client is gone and the session still works at the gateway."""
    if client_alive(state):
        return True
    summarize_session(label, state)
    session_id = state.get("session_id")
    return bool(session_row(session_id).get("last_activity_description")
                and gateway_running("state", session_id) is not False)


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


def active_sessions():
    """Every session Hermes' store shows at work, whoever started it. A row can outlive its work: confirm it."""
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    try:
        db = sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True, timeout=2)
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute("select id, source, cwd, last_activity_description from sessions "
                              "where coalesce(last_activity_description, '') != ''").fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    return [{"id": row["id"], "source": row["source"], "cwd": row["cwd"],
             "activity": row["last_activity_description"]} for row in rows]


def refuse_if_running():
    """One Hermes session works at a time: a delegated run, or any other session the gateway has at work."""
    root = state_root()
    labels, running = {}, []
    for state_file in root.glob("*/state.json") if root.exists() else ():
        label, state = state_file.parent.name, json.loads(state_file.read_text())
        summarize_session(label, state)
        labels[state.get("session_id")] = label
        if client_alive(state):
            running.append(label)
    for session in active_sessions():
        label = labels.get(session["id"])
        if label not in running and gateway_running("state", session["id"]) is not False:
            running.append(label or f"session {session['id']} ({session['source']}, {session['cwd']})")
    if running:
        raise DelegateError(f"a turn is already running: {', '.join(running)}. "
                            "Wait for it, or stop it first: one Hermes session works at a time")


def guard_installed():
    path = config_path()
    if not path.exists():
        return False
    text = path.read_text()
    return GUARD_BEGIN in text and str(GUARD) in text and f'matcher: "{GUARD_MATCHER}"' in text


def check_ready(root, config):
    command = config.get("ready_command")
    if not command:
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, cwd=root)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:400]
        raise DelegateError(f"not ready: {detail or f'ready_command exited {result.returncode}'}")


def check_brief(path):
    text = path.read_text()
    missing = [name for name in REQUIRED_SECTIONS
               if not re.search(rf"^#+\s*{name}\b", text, re.M | re.I)]
    if missing:
        raise DelegateError(f"brief lacks required sections: {', '.join(missing)}")


def launch(label, state, brief, args):
    """Check and spawn under one lock, so simultaneous calls cannot both pass the check."""
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return launch_locked(label, state, brief, args)


def launch_locked(label, state, brief, args):
    refuse_if_running()
    if not guard_installed():
        raise DelegateError(f"the guard is not in {config_path()}: run install-guard first")
    root, config = find_project(Path(state["cwd"]))
    check_ready(root, config)

    directory = run_dir(label)
    directory.mkdir(parents=True, exist_ok=True)
    turn = state.get("turn", 0) + 1
    brief_copy = directory / f"brief-{turn}.md"
    shutil.copyfile(brief, brief_copy)

    command = [hermes_binary(), "chat", "--query-file", str(brief_copy), "-Q", "--format", "stream-json",
               "--in", state["cwd"], "--source", "tool", "--accept-hooks"]
    if args.max_turns is not None:  # Hermes refuses a different limit on resume, so send passes one only when told to
        command += ["--max-turns", str(args.max_turns)]
    if config.get("provider"):
        command += ["--provider", config["provider"], "-m", config["model"]]
    if args.skills:
        command += ["--skills", args.skills]
    if state.get("session_id"):
        command += ["--resume", state["session_id"]]

    with turn_log(label, turn).open("wb") as stdout, (directory / f"turn-{turn}.stderr").open("wb") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=True, close_fds=True, cwd=state["cwd"])
    state.update(turn=turn, pid=process.pid, pid_start=process_start(process.pid), watch_offset=0, watch_turn=turn,
                 started_at=time.time(),
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
    if args.max_turns is None:
        args.max_turns = DEFAULT_MAX_TURNS
    return launch(args.label, {"label": args.label, "cwd": str(Path(args.cwd).resolve())}, brief, args)


def cmd_send(args):
    state = load_state(args.label)
    refuse_if_running()
    summarize_session(args.label, state)
    if not state.get("session_id"):
        raise DelegateError("the last turn recorded no session_id: nothing to resume")
    return launch(args.label, state, Path(args.brief), args)


def summarize_session(label, state):
    turn = state.get("turn", 0)
    try:  # Hermes names the session on stderr at once; its events carry the id only in the final result
        named = SESSION_LINE.findall((run_dir(label) / f"turn-{turn}.stderr").read_text(errors="replace"))
    except OSError:
        named = []
    if named:
        state["session_id"] = named[-1]
    for event in read_events(turn_log(label, turn)):
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


def read_cursor(label, state, turn):
    try:
        return int((run_dir(label) / f"turn-{turn}.cursor").read_text())
    except (OSError, ValueError):
        return state.get("watch_offset", 0) if state.get("watch_turn") == turn else 0


def save_cursor(label, turn, offset):
    """Each watcher writes its own temporary file, and none touches state.json, so watchers cannot collide."""
    path = run_dir(label) / f"turn-{turn}.cursor"
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(str(offset))
    temporary.replace(path)


def last_result(path):
    return next((event for event in reversed(read_events(path)) if event.get("type") == "result"), None)


def running_line(state, path, written, new_calls, last_tool):
    calls = sum(event.get("type") == "tool_use" for event in read_events(path))
    age = round(time.time() - path.stat().st_mtime)
    parts = [f"RUNNING elapsed_s={round(time.time() - state.get('started_at', time.time()))}",
             f"calls={calls}", f"new_calls={new_calls}", f"last_event_age_s={age}"]
    if written:
        shown = sorted(written)[:DIGEST_PATHS]
        extra = f",+{len(written) - len(shown)}" if len(written) > len(shown) else ""
        parts.append(f"writes={','.join(shown)}{extra}")
    if last_tool:
        parts.append(f"last={last_tool}")
    return " ".join(parts)


def cmd_watch(args):
    state = load_state(args.label)
    turn = state["turn"]
    path = turn_log(args.label, turn)
    offset = read_cursor(args.label, state, turn)
    deadline = time.time() + args.timeout
    last_seen = path.stat().st_mtime  # survives a restart, so short watches still notice long silence
    flagged = False
    written, new_calls, last_tool = set(), 0, ""
    prefix = state["cwd"].rstrip("/") + "/"

    if offset and offset >= path.stat().st_size:  # already read to the end: a lingering client must not hold us
        final = last_result(path)
        if final is not None:
            print_event(final)
            return 0 if final.get("exit_code") == 0 else 1

    while True:
        alive = client_alive(state)
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
        complete = chunk.rfind(b"\n") + 1  # a line still being written stays for the next read
        if complete:
            for line in chunk[:complete].splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "tool_use":
                    new_calls += 1
                    last_tool = f"{event.get('name')}: {shorten(tool_input_preview(event), 120)}"
                    data = event.get("input") if isinstance(event.get("input"), dict) else {}
                    if event.get("name") in ("write_file", "patch") and data.get("path"):
                        written.add(str(data["path"]).removeprefix(prefix))
                    if args.digest:
                        continue
                if print_event(event):
                    save_cursor(args.label, turn, offset + complete)
                    return 0 if event.get("exit_code") == 0 else 1
            offset += complete
            save_cursor(args.label, turn, offset)
            last_seen, flagged = time.time(), False
        if not alive and not complete:
            final = last_result(path)
            if final is not None:  # already shown by an earlier watch
                print_event(final)
                return 0 if final.get("exit_code") == 0 else 1
            stderr = run_dir(args.label) / f"turn-{turn}.stderr"
            tail = stderr.read_text(errors="replace")[-400:] if stderr.exists() else ""
            print(f"DIED: the process ended with no result record. {shorten(tail, 400)}", flush=True)
            return 1
        if not flagged and time.time() - last_seen > args.quiet_after:
            flagged = True
            print(f"SILENT: no event for {args.quiet_after} s. Long thinking, or writing a large file, also looks "
                  "like this; check where the model runs before acting.", flush=True)
        remaining = deadline - time.time()
        if remaining <= 0:
            print(running_line(state, path, written, new_calls, last_tool), flush=True)
            return EXIT_STILL_RUNNING
        time.sleep(min(args.poll, remaining))


def cmd_stop(args):
    state = load_state(args.label)
    deadline = time.time() + args.wait
    summarize_session(args.label, state)

    def signal_client():
        try:
            os.killpg(state["pid"], signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass

    def wait_client(until):
        while time.time() < until and client_alive(state):
            time.sleep(0.2)

    if not state.get("session_id") and client_alive(state):  # no id yet: the client may still be starting
        signal_client()
        wait_client(min(deadline, time.time() + CLIENT_GRACE))
        summarize_session(args.label, state)
    session_id = state.get("session_id")
    running = False
    if session_id:
        running = gateway_running("interrupt", session_id)
        while running and time.time() < deadline:
            time.sleep(0.5)
            running = gateway_running("state", session_id)
    wait_client(min(deadline, time.time() + CLIENT_GRACE))  # a cancelled turn makes the client report and exit
    if client_alive(state):
        signal_client()
        wait_client(deadline)
    if running is None:
        print(f"{args.label}: could not confirm the stop with the gateway; treat session {session_id} as running")
        return 1
    if running or client_alive(state):
        print(f"{args.label}: still running after {args.wait} s, session {session_id}")
        return 1
    print(f"{args.label}: stopped")
    return 0


def cmd_sessions(_args):
    """What is alive right now: the gateway, clients, watchers and sessions at work. Reads only."""
    clients, watchers = [], []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            words = (entry / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
        if "chat" in words and "--query-file" in words:
            brief = words[words.index("--query-file") + 1]
            clients.append({"pid": int(entry.name), "label": Path(brief).parent.name})
        elif "watch" in words and any(Path(word).name == "delegate.py" for word in words):
            watchers.append({"pid": int(entry.name), "label": words[words.index("watch") + 1]})
    sessions = [{**session, "running": gateway_running("state", session["id"])} for session in active_sessions()]
    print(json.dumps({"clients": clients, "watchers": watchers, "active_sessions": sessions}, indent=2))
    return 0


def cmd_report(args):
    state = load_state(args.label)
    result = last_result(turn_log(args.label, state["turn"]))
    if result is None:
        raise DelegateError(f"no result in turn {state['turn']} yet")
    print(result.get("text") or result.get("error") or f"empty report, exit {result.get('exit_code')}")
    return 0


def cmd_snapshot(args):
    state = load_state(args.label)
    if run_is_live(args.label, state):
        raise DelegateError(f"{args.label} is still running: stop it or wait for its result")
    cwd = Path(state["cwd"])

    def git(*options):
        try:
            return subprocess.run(["git", "-C", str(cwd), *options], check=True, capture_output=True).stdout
        except subprocess.CalledProcessError as error:
            raise DelegateError(f"git {options[0]}: {error.stderr.decode(errors='replace').strip()}") from error

    base = git("rev-parse", "--verify", f"{args.base}^{{commit}}").decode().strip()
    listed = (git("diff", "--name-only", "--relative", "-z", base) + b"\0"
              + git("ls-files", "--others", "--exclude-standard", "-z")).decode()
    paths = sorted(set(filter(None, listed.split("\0"))))
    dest = Path(args.dest) if args.dest else Path(tempfile.mkdtemp(prefix=f"{args.label}-"))
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise DelegateError(f"{dest} is not empty")
    extract = subprocess.run(["tar", "-x", "-C", str(dest)], input=git("archive", base), capture_output=True)
    if extract.returncode:
        raise DelegateError(f"tar: {extract.stderr.decode(errors='replace').strip()}")
    files = []
    for relative in paths:
        source, target = cwd / relative, dest / relative
        if source.is_symlink() or source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
            content = os.readlink(source).encode() if source.is_symlink() else source.read_bytes()
            files.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
        elif source.exists():
            raise DelegateError(f"cannot copy {relative}: not a file")
        else:
            target.unlink(missing_ok=True)
            files.append({"path": relative, "sha256": None})
    fingerprint = hashlib.sha256(json.dumps({"base": base, "files": files}, sort_keys=True).encode()).hexdigest()
    manifest = run_dir(args.label) / f"snapshot-{fingerprint[:12]}.json"
    manifest.write_text(json.dumps({"base": base, "snapshot": str(dest), "fingerprint": fingerprint, "files": files},
                                   indent=2))
    print(json.dumps({"base": base, "snapshot": str(dest), "files": len(files), "fingerprint": fingerprint,
                      "manifest": str(manifest)}))
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
            "tools": dict(collections.Counter(event.get("name") for event in events
                                              if event.get("type") == "tool_use")),
        })
        final_result = result
    row = session_row(state.get("session_id"))
    alive = client_alive(state)
    if alive:
        status = "running"
    elif row.get("last_activity_description"):
        status = "detached"  # the client is gone and the session still works at the gateway
    elif final_result is not None:
        status = "finished"
    else:
        status = "crashed"
    summary = {"label": label, "state": status, "session_id": state.get("session_id"), "cwd": state.get("cwd"),
               "total_input_tokens": total_input, "total_output_tokens": total_output, "turns": turns}
    if alive:
        summary["elapsed_s"] = round(time.time() - state.get("started_at", time.time()))
        summary["last_event_age_s"] = round(time.time() - turn_log(label, state["turn"]).stat().st_mtime)
    if row:
        summary["session"] = {"api_calls": row.get("api_call_count"), "input_tokens": row.get("input_tokens"),
                              "output_tokens": row.get("output_tokens"), "cache_read_tokens": row.get("cache_read_tokens"),
                              "activity": row.get("last_activity_description"),
                              "last_activity_age_s": round(time.time() - (row.get("last_activity_at") or time.time()))}
    return summary


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
    block = (f"{GUARD_BEGIN}\nhooks:\n  pre_tool_call:\n"
             f'    - matcher: "{GUARD_MATCHER}"\n'
             f'      command: "{shlex.quote(sys.executable)} {shlex.quote(str(GUARD))}"\n'
             f"      timeout: 10\n      fail_closed: true\n{GUARD_END}\n")
    if GUARD_BEGIN in text:
        if guard_installed():
            print(f"guard already in {path}")
            return 0
        updated = re.sub(rf"{re.escape(GUARD_BEGIN)}.*?{re.escape(GUARD_END)}\n", lambda _: block, text, flags=re.S)
        path.write_text(updated)
        print(f"guard updated in {path}")
        return 0
    if re.search(r"^hooks:", text, re.M):
        raise DelegateError(f"{path} already has a hooks: block; merge this by hand: {GUARD}")
    backup = Path(str(path) + ".bak-before-delegate")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(f"{text}\n{block}")
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
        sub.add_argument("--max-turns", type=int,
                         help=f"tool-call iterations (start: default {DEFAULT_MAX_TURNS}; send: keeps the session's)")
        sub.add_argument("--skills", help="Hermes skills to preload, comma separated")

    start = commands.add_parser("start", help="start a new session from a full brief")
    turn_options(start)
    start.add_argument("--cwd", default=".", help="directory the agent works in")
    start.set_defaults(func=cmd_start)

    send = commands.add_parser("send", help="send a correction to the same session")
    turn_options(send)
    send.set_defaults(func=cmd_send)

    watch = commands.add_parser("watch", help="print new events; exit 0 or 1 when the turn ends, 3 at the deadline")
    watch.add_argument("label")
    watch.add_argument("--timeout", type=float, default=DEFAULT_WATCH_TIMEOUT,
                       help="return by this many seconds even if the turn still runs (default: %(default)s)")
    watch.add_argument("--digest", action="store_true", help="one summary line instead of a line per tool call")
    watch.add_argument("--quiet-after", type=int, default=600, help="flag this many seconds of silence")
    watch.add_argument("--poll", type=float, default=0.5)
    watch.set_defaults(func=cmd_watch)

    stop = commands.add_parser("stop", help="interrupt the turn at the gateway; reports stopped only once confirmed")
    stop.add_argument("label")
    stop.add_argument("--wait", type=int, default=60)
    stop.set_defaults(func=cmd_stop)

    commands.add_parser("sessions", help="clients, watchers and Hermes sessions alive right now").set_defaults(
        func=cmd_sessions)

    report = commands.add_parser("report", help="print the whole final text of the last turn")
    report.add_argument("label")
    report.set_defaults(func=cmd_report)

    snapshot = commands.add_parser("snapshot", help="copy an ended turn's delivery, committed or not, for verification")
    snapshot.add_argument("label")
    snapshot.add_argument("--base", required=True, help="the revision the delivery started from")
    snapshot.add_argument("--dest", help="an empty directory; default: a new temporary one")
    snapshot.set_defaults(func=cmd_snapshot)

    status = commands.add_parser("status", help="state, tokens and tool counts as JSON")
    status.add_argument("label", nargs="?")
    status.set_defaults(func=cmd_status)

    commands.add_parser("install-guard", help="add the project guard to Hermes' config").set_defaults(
        func=cmd_install_guard)
    commands.add_parser("uninstall-guard", help="remove the project guard").set_defaults(func=cmd_uninstall_guard)

    args = parser.parse_args(argv)
    try:
        check_not_sandboxed()
        return args.func(args)
    except DelegateError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
