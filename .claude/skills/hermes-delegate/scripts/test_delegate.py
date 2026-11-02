"""Tests for delegate.py and guard_hook.py. They use a fake hermes: no model, no GPU."""
import contextlib
import io
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DELEGATE = HERE / "delegate.py"
GUARD = HERE / "guard_hook.py"

GUARD_RULES = {
    "blocked_commands": [
        {"pattern": "^flashtool", "reason": "flashing is the user's"},
        {"pattern": r"^python[0-9.]* .*\bflashtool\b", "reason": "flashing is the user's"},
        {"pattern": r"^fwtool .*\b(flash|erase[-_]flash)\b", "reason": "flashing is the user's"},
        {"pattern": r"^docker .*\b(stop|kill|rm|restart|down)\b", "reason": "other services"},
        {"pattern": r"^python[0-9.]* .*svc_ctl.*\b(stop|use)\b", "reason": "the service"},
    ],
    "blocked_code": [{"pattern": r"\bflashtool\b", "reason": "flashing is the user's"}],
    "secret_paths": [r"(^|[\s/'\"=])(\.env|prod\.env|\.config\.yaml)(?![\w.-])"],
    "protected_paths": [r"(^|/)settings[^/]*$", r"(^|/)build/", r"(^|/)main/boards/(?!mine/)"],
}

BRIEF = textwrap.dedent("""\
    ## Goal
    Add a thing.
    ## Files
    a.py
    ## Tests
    test_a
    ## Verify
    python3 -m unittest
    ## Forbidden
    Everything else.
    ## Report
    Commit and test output.
    """)

FAKE_HERMES = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, signal, sys, time
    from pathlib import Path
    gateway = Path(os.environ["FAKE_ARGS"]).with_name("gateway-running")
    if sys.argv[1:2] == ["--run-module"]:  # the session helper, run inside Hermes' runtime
        with open(os.environ["FAKE_ARGS"] + ".helper", "a") as f:
            f.write(json.dumps(sys.argv[1:]) + "\\n")
        if os.environ.get("FAKE_HELPER") == "broken":
            sys.exit(1)
        if sys.argv[-2] == "interrupt" and os.environ.get("FAKE_HELPER") != "stuck":
            gateway.unlink(missing_ok=True)
        print(json.dumps({"running": gateway.exists()}))
        sys.exit(0)
    with open(os.environ["FAKE_ARGS"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    mode = os.environ.get("FAKE_MODE", "ok")
    session = "sess-fake"
    def emit(**event):
        print(json.dumps({"timestamp": 1, **event}), flush=True)
    def interrupted(*_):
        if mode == "gateway":  # the real client only detaches: the session keeps running
            print("Detached; accepted work continues at the gateway.", file=sys.stderr, flush=True)
            emit(type="result", session_id="", exit_code=130, text="", duration_ms=5,
                 tokens={"input": 0, "output": 0, "total": 0})
        else:
            emit(type="result", session_id=session, exit_code=130, text="", duration_ms=5,
                 tokens={"input": 1, "output": 1, "total": 2})
        sys.exit(130)
    signal.signal(signal.SIGINT, interrupted)
    emit(type="system", subtype="init", model="fake", session_id="")
    print("Session: " + session, file=sys.stderr, flush=True)
    if mode in ("gateway", "crash_gateway"):
        gateway.touch()
    if mode in ("crash", "crash_gateway"):
        os._exit(3)
    emit(type="tool_use", name="terminal", input={"command": "git status"})
    emit(type="tool_result", name="terminal", output="BLOCKED: nope", duration_ms=4, is_error=True)
    emit(type="text", text="working")
    if mode == "partial":
        sys.stdout.write('{"timestamp": 1, "type": "tool_use", "name": "split"')
        sys.stdout.flush()
        time.sleep(1)
        print(', "input": {}}', flush=True)
    if mode == "chatty":
        for number in range(400):
            emit(type="tool_use", name="read_file", input={"path": f"f{number}"})
    if mode == "writes":
        emit(type="tool_use", name="write_file", input={"path": "src/a.c", "content": "x"})
        emit(type="tool_use", name="patch", input={"path": "src/b.h", "new_string": "y"})
        time.sleep(30)
    if mode == "hang":
        time.sleep(30)
    if mode == "gateway":
        while gateway.exists():
            time.sleep(0.05)
        emit(type="result", session_id=session, exit_code=130, text="", duration_ms=5,
             tokens={"input": 3, "output": 4, "total": 7})
        sys.exit(130)
    text = "R" * 5000 + " END-OF-REPORT" if mode == "long_report" else "Done: abc123"
    emit(type="result", session_id=session, exit_code=0, text=text, duration_ms=7,
         tokens={"input": 10, "output": 20, "total": 30})
    if mode == "linger":
        time.sleep(30)
    """)

READY = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os, sys
    print(os.environ.get("FAKE_READY_MESSAGE", "ready"))
    sys.exit(int(os.environ.get("FAKE_READY_EXIT", "0")))
    """)


class DelegateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.fake = root / "hermes"
        self.fake.write_text(FAKE_HERMES)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.ready = root / "ready.py"
        self.ready.write_text(READY)
        self.project = root / "project"
        self.project.mkdir()
        self.project_config = {"provider": "prov", "model": "mod-1",
                               "ready_command": [sys.executable, str(self.ready)]}
        self.write_project_config()
        self.config = root / "config.yaml"
        self.config.write_text("model:\n  default: other\n")
        self.args = root / "args.jsonl"
        self.brief = root / "brief.md"
        self.brief.write_text(BRIEF)
        self.env = {
            **os.environ,
            "HERMES_DELEGATE_HERMES": str(self.fake),
            "HERMES_DELEGATE_STATE": str(root / "state"),
            "HERMES_DELEGATE_CONFIG": str(self.config),
            "FAKE_ARGS": str(self.args),
            "HERMES_HOME": str(root / "hermes-home"),
        }
        self.root = root
        self.addCleanup(self.kill_runs)

    def write_project_config(self):
        (self.project / ".hermes-delegate.json").write_text(json.dumps(self.project_config))

    def kill_runs(self):
        for state in (self.root / "state").glob("*/state.json"):
            pid = json.loads(state.read_text()).get("pid")
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    def run_cmd(self, *args, env=None, timeout=20):
        return subprocess.run([sys.executable, str(DELEGATE), *args], capture_output=True, text=True,
                              env={**self.env, **(env or {})}, timeout=timeout)

    def install(self):
        result = self.run_cmd("install-guard")
        self.assertEqual(result.returncode, 0, result.stderr)

    def start(self, label="t1", env=None):
        return self.run_cmd("start", label, "--brief", str(self.brief), "--cwd", str(self.project), env=env)

    def calls(self):
        return [json.loads(line) for line in self.args.read_text().splitlines()]

    def wait_finished(self, label="t1"):
        result = self.run_cmd("watch", label, "--quiet-after", "60")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_refuses_without_guard(self):
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("install-guard", result.stderr)
        self.assertFalse(self.args.exists())

    def test_install_guard_appends_and_is_idempotent(self):
        self.install()
        first = self.config.read_text()
        self.assertIn("pre_tool_call", first)
        self.assertIn("guard_hook.py", first)
        self.assertIn("fail_closed: true", first)
        self.assertTrue(first.startswith("model:\n  default: other\n"))
        self.assertTrue(Path(str(self.config) + ".bak-before-delegate").exists())
        self.install()
        self.assertEqual(self.config.read_text(), first)

    def test_install_guard_refuses_existing_hooks_block(self):
        self.config.write_text("hooks:\n  pre_llm_call: []\n")
        result = self.run_cmd("install-guard")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), "hooks:\n  pre_llm_call: []\n")

    def test_uninstall_guard_restores_config(self):
        original = self.config.read_text()
        self.install()
        self.assertEqual(self.run_cmd("uninstall-guard").returncode, 0)
        self.assertEqual(self.config.read_text(), original)

    def test_refuses_brief_missing_a_section(self):
        self.install()
        self.brief.write_text(BRIEF.replace("## Verify\npython3 -m unittest\n", ""))
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Verify", result.stderr)
        self.assertFalse(self.args.exists())

    def test_refuses_when_not_ready_and_shows_why(self):
        self.install()
        result = self.start(env={"FAKE_READY_EXIT": "1", "FAKE_READY_MESSAGE": "backend is busy"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backend is busy", result.stderr)
        self.assertFalse(self.args.exists())

    def test_runs_without_ready_command(self):
        self.install()
        del self.project_config["ready_command"]
        self.write_project_config()
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()

    def test_local_config_supplies_the_route(self):
        self.install()
        (self.project / ".hermes-delegate.json").write_text('{"guard": {}}')
        (self.project / ".hermes-delegate.local.json").write_text(json.dumps(self.project_config))
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()
        args = self.calls()[0]
        self.assertEqual(args[args.index("--provider") + 1], "prov")

    def test_local_config_overrides_the_shared_one(self):
        self.install()
        (self.project / ".hermes-delegate.local.json").write_text('{"model": "mod-2"}')
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()
        args = self.calls()[0]
        self.assertEqual(args[args.index("-m") + 1], "mod-2")

    def test_hermes_default_route_must_be_chosen_explicitly(self):
        self.install()
        self.project_config = {"use_hermes_default": True}
        self.write_project_config()
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()
        args = self.calls()[0]
        self.assertNotIn("--provider", args)
        self.assertNotIn("-m", args)

    def test_provider_without_model_is_refused(self):
        self.install()
        self.project_config = {"provider": "prov"}
        self.write_project_config()
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provider and model", result.stderr)

    def test_refuses_without_project_config(self):
        self.install()
        (self.project / ".hermes-delegate.json").unlink()
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".hermes-delegate.json", result.stderr)
        self.assertFalse(self.args.exists())

    def test_refuses_config_without_a_route(self):
        self.install()
        self.project_config = {"guard": {}}
        self.write_project_config()
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("use_hermes_default", result.stderr)
        self.assertFalse(self.args.exists())

    def test_finds_config_in_a_parent_directory(self):
        self.install()
        nested = self.project / "sub" / "dir"
        nested.mkdir(parents=True)
        result = self.run_cmd("start", "t1", "--brief", str(self.brief), "--cwd", str(nested))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_finished()

    def test_start_pins_configured_model_and_never_uses_oneshot_z(self):
        self.install()
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()
        args = self.calls()[0]
        self.assertEqual(args[0], "chat")
        for flag in ("-Q", "--accept-hooks"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--format") + 1], "stream-json")
        self.assertEqual(args[args.index("--provider") + 1], "prov")
        self.assertEqual(args[args.index("-m") + 1], "mod-1")
        self.assertEqual(args[args.index("--in") + 1], str(self.project))
        self.assertIn("--query-file", args)
        self.assertNotIn("-z", args)
        self.assertNotIn("--yolo", args)
        # The gateway-backed chat path refuses this flag outright.
        self.assertNotIn("--run-budget", args)
        self.assertNotIn("--resume", args)

    def test_watch_reports_tools_errors_and_result_then_exits(self):
        self.install()
        self.start()
        out = self.wait_finished().stdout
        self.assertIn("TOOL terminal", out)
        self.assertIn("git status", out)
        self.assertIn("ERROR terminal", out)
        self.assertIn("BLOCKED", out)
        self.assertIn("RESULT exit=0", out)
        self.assertIn("Done: abc123", out)
        self.assertNotIn("working\n", out)

    def test_second_watch_only_shows_new_events(self):
        self.install()
        self.start()
        self.wait_finished()
        out = self.wait_finished().stdout
        self.assertNotIn("TOOL terminal", out)
        self.assertIn("RESULT exit=0", out)

    def test_send_resumes_recorded_session(self):
        self.install()
        self.start()
        self.wait_finished()
        correction = self.root / "fix.md"
        correction.write_text("Fix the edge case.\n")
        result = self.run_cmd("send", "t1", "--brief", str(correction))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.wait_finished()
        args = self.calls()[1]
        self.assertEqual(args[args.index("--resume") + 1], "sess-fake")
        self.assertEqual(args[args.index("--provider") + 1], "prov")

    def test_refuses_second_run_while_one_is_live(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        for command in (self.start("t2", env={"FAKE_MODE": "hang"}),
                        self.run_cmd("send", "t1", "--brief", str(self.brief))):
            self.assertNotEqual(command.returncode, 0)
            self.assertIn("running", command.stderr)

    def test_stop_interrupts_and_records_result(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        deadline = time.time() + 10
        while time.time() < deadline:
            if "tool_use" in "".join(p.read_text() for p in (self.root / "state/t1").glob("turn-*.jsonl")):
                break
            time.sleep(0.05)
        time.sleep(0.3)
        result = self.run_cmd("stop", "t1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = json.loads(self.run_cmd("status", "t1").stdout)
        self.assertEqual(status["state"], "finished")
        self.assertEqual(status["turns"][-1]["exit_code"], 130)

    def test_crash_without_result_is_reported(self):
        self.install()
        self.start(env={"FAKE_MODE": "crash"})
        result = self.run_cmd("watch", "t1", "--quiet-after", "60")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DIED", result.stdout)
        self.assertEqual(json.loads(self.run_cmd("status", "t1").stdout)["state"], "crashed")

    def test_watch_flags_silence(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        process = subprocess.Popen([sys.executable, str(DELEGATE), "watch", "t1", "--quiet-after", "1"],
                                   stdout=subprocess.PIPE, text=True, env=self.env)
        deadline = time.time() + 10
        seen = ""
        while time.time() < deadline and "SILENT" not in seen:
            seen += process.stdout.readline()
        process.kill()
        process.stdout.close()
        process.wait()
        self.assertIn("SILENT", seen)

    def test_status_totals_tokens_and_tools(self):
        self.install()
        self.start()
        self.wait_finished()
        status = json.loads(self.run_cmd("status", "t1").stdout)
        self.assertEqual(status["session_id"], "sess-fake")
        self.assertEqual(status["state"], "finished")
        self.assertEqual(status["total_output_tokens"], 20)
        self.assertEqual(status["turns"][0]["tool_calls"], 1)
        self.assertEqual(status["turns"][0]["tool_errors"], 1)

    def wait_for_event(self, text, label="t1", timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text in "".join(p.read_text() for p in (self.root / "state" / label).glob("turn-*.jsonl")):
                return
            time.sleep(0.02)
        self.fail(f"no {text} in the log")

    def gateway_marker(self):
        return self.args.with_name("gateway-running")

    def session_row(self, description, session="sess-fake"):
        home = self.root / "hermes-home"
        home.mkdir(exist_ok=True)
        db = sqlite3.connect(home / "state.db")
        db.execute("create table if not exists sessions (id text, source text, cwd text, api_call_count int, "
                   "input_tokens int, output_tokens int, cache_read_tokens int, last_activity_at real, "
                   "last_activity_description text)")
        db.execute("delete from sessions")
        db.execute("insert into sessions values (?, 'tool', '/work', 7, 100, 50, 900, ?, ?)",
                   (session, time.time() - 12, description))
        db.commit()
        db.close()

    def test_watch_returns_at_its_deadline_while_the_turn_runs(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        began = time.time()
        result = self.run_cmd("watch", "t1", "--timeout", "1")
        self.assertLess(time.time() - began, 4)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("RUNNING", result.stdout)
        self.assertIn("calls=1", result.stdout)
        self.assertEqual(json.loads(self.run_cmd("status", "t1").stdout)["state"], "running")

    def test_watch_timeout_zero_reads_once_and_returns(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        self.wait_for_event("tool_use")
        began = time.time()
        result = self.run_cmd("watch", "t1", "--timeout", "0")
        self.assertLess(time.time() - began, 2)
        self.assertEqual(result.returncode, 3)
        self.assertIn("TOOL terminal", result.stdout)

    def test_watch_is_bounded_by_default(self):
        out = self.run_cmd("watch", "--help").stdout
        self.assertIn("--timeout", out)
        self.assertRegex(" ".join(out.split()), r"default:? 1800")

    def test_watch_continues_after_its_deadline_without_repeating(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        self.wait_for_event("tool_use")
        self.assertIn("TOOL terminal", self.run_cmd("watch", "t1", "--timeout", "0").stdout)
        self.assertNotIn("TOOL terminal", self.run_cmd("watch", "t1", "--timeout", "0").stdout)

    def test_a_line_cut_by_the_deadline_is_read_whole_next_time(self):
        self.install()
        self.start(env={"FAKE_MODE": "partial"})
        self.wait_for_event('"split"')
        first = self.run_cmd("watch", "t1", "--timeout", "0").stdout
        second = self.run_cmd("watch", "t1", "--timeout", "5").stdout
        self.assertIn("TOOL split", first + second)

    def test_digest_summarizes_tools_and_keeps_errors(self):
        self.install()
        self.start(env={"FAKE_MODE": "writes"})
        self.wait_for_event("src/b.h")
        out = self.run_cmd("watch", "t1", "--timeout", "0", "--digest").stdout
        self.assertNotIn("TOOL ", out)
        self.assertIn("ERROR terminal", out)
        self.assertIn("calls=3", out)
        self.assertIn("src/a.c", out)
        self.assertIn("src/b.h", out)

    def test_second_watch_returns_when_the_client_lingers_after_its_result(self):
        self.install()
        self.start(env={"FAKE_MODE": "linger"})
        self.assertEqual(self.run_cmd("watch", "t1", "--timeout", "5").returncode, 0)
        began = time.time()
        result = self.run_cmd("watch", "t1", "--timeout", "5")
        self.assertLess(time.time() - began, 2)
        self.assertEqual(result.returncode, 0)
        self.assertIn("RESULT exit=0", result.stdout)

    def test_recycled_pid_is_not_taken_for_the_turn(self):
        self.install()
        self.start()
        self.wait_finished()
        other = subprocess.Popen(["sleep", "30"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        path = self.root / "state/t1/state.json"
        state = json.loads(path.read_text())
        state["pid"] = other.pid
        path.write_text(json.dumps(state))
        self.assertEqual(json.loads(self.run_cmd("status", "t1").stdout)["state"], "finished")
        self.assertEqual(self.start("t2").returncode, 0)

    def test_concurrent_watchers_all_finish_cleanly(self):
        self.install()
        self.start(env={"FAKE_MODE": "chatty"})
        watchers = [subprocess.Popen([sys.executable, str(DELEGATE), "watch", "t1", "--poll", "0.001"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
                    for _ in range(6)]
        for watcher in watchers:
            out, err = watcher.communicate(timeout=20)
            self.assertEqual(watcher.returncode, 0, err)
            self.assertIn("RESULT exit=0", out)

    def test_watch_leaves_the_run_state_alone(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        self.wait_for_event("tool_use")
        path = self.root / "state/t1/state.json"
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.run_cmd("watch", "t1", "--timeout", "0")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_killing_the_watcher_leaves_the_turn_running(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        watcher = subprocess.Popen([sys.executable, str(DELEGATE), "watch", "t1"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env)
        self.wait_for_event("tool_use")
        watcher.kill()
        watcher.wait()
        self.assertEqual(json.loads(self.run_cmd("status", "t1").stdout)["state"], "running")

    def test_session_id_is_known_while_the_turn_runs_and_after_a_crash(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        self.wait_for_event("tool_use")
        self.assertEqual(json.loads(self.run_cmd("status", "t1").stdout)["session_id"], "sess-fake")
        self.run_cmd("stop", "t1", "--wait", "5")
        self.start("t2", env={"FAKE_MODE": "crash"})
        self.run_cmd("watch", "t2", "--timeout", "5")
        result = self.run_cmd("send", "t2", "--brief", str(self.brief))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.run_cmd("watch", "t2", "--timeout", "5")
        args = self.calls()[-1]
        self.assertEqual(args[args.index("--resume") + 1], "sess-fake")

    def test_stop_really_stops_a_session_running_at_the_gateway(self):
        self.install()
        self.start(env={"FAKE_MODE": "gateway"})
        self.wait_for_event("tool_use")
        result = self.run_cmd("stop", "t1", "--wait", "5")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.gateway_marker().exists(), "the session is still running at the gateway")
        self.assertIn("stopped", result.stdout)
        helper = [json.loads(line) for line in Path(str(self.args) + ".helper").read_text().splitlines()]
        self.assertEqual(helper[0][:4], ["--run-module", "cProfile", "-o", os.devnull])
        self.assertEqual(helper[0][-2:], ["interrupt", "sess-fake"])

    def test_stop_acts_on_the_gateway_even_when_the_client_is_gone(self):
        self.install()
        self.start(env={"FAKE_MODE": "crash_gateway"})
        self.run_cmd("watch", "t1", "--timeout", "5")
        self.assertTrue(self.gateway_marker().exists())
        result = self.run_cmd("stop", "t1", "--wait", "5")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.gateway_marker().exists())

    def test_stop_never_claims_success_when_the_gateway_keeps_running(self):
        self.install()
        self.start(env={"FAKE_MODE": "gateway"})
        self.wait_for_event("tool_use")
        result = self.run_cmd("stop", "t1", "--wait", "1", env={"FAKE_HELPER": "stuck"})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn(": stopped", result.stdout)
        self.assertIn("still running", result.stdout)

    def test_stop_with_the_client_gone_still_reports_a_running_gateway(self):
        self.install()
        self.start(env={"FAKE_MODE": "crash_gateway"})
        self.run_cmd("watch", "t1", "--timeout", "5")
        result = self.run_cmd("stop", "t1", "--wait", "1", env={"FAKE_HELPER": "stuck"})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("still running", result.stdout)

    def test_stop_says_when_it_cannot_confirm(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        self.wait_for_event("tool_use")
        result = self.run_cmd("stop", "t1", "--wait", "3", env={"FAKE_HELPER": "broken"})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn(": stopped", result.stdout)
        self.assertIn("could not confirm", result.stdout)

    def test_a_new_turn_refuses_while_the_session_runs_at_the_gateway(self):
        self.install()
        self.start(env={"FAKE_MODE": "crash_gateway"})
        self.run_cmd("watch", "t1", "--timeout", "5")
        self.session_row("receiving stream response")
        for command in (self.run_cmd("send", "t1", "--brief", str(self.brief)), self.start("t2")):
            self.assertNotEqual(command.returncode, 0, command.stdout)
            self.assertIn("t1", command.stderr)
            self.assertIn("running", command.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_status_reports_a_detached_session_and_its_real_usage(self):
        self.install()
        self.start(env={"FAKE_MODE": "crash_gateway"})
        self.run_cmd("watch", "t1", "--timeout", "5")
        self.session_row("receiving stream response")
        status = json.loads(self.run_cmd("status", "t1").stdout)
        self.assertEqual(status["state"], "detached")
        self.assertEqual(status["session"]["api_calls"], 7)
        self.assertEqual(status["session"]["cache_read_tokens"], 900)
        self.assertGreaterEqual(status["session"]["last_activity_age_s"], 12)

    def test_status_of_a_running_turn_gives_progress(self):
        self.install()
        self.start(env={"FAKE_MODE": "writes"})
        self.wait_for_event("src/b.h")
        status = json.loads(self.run_cmd("status", "t1").stdout)
        self.assertGreaterEqual(status["elapsed_s"], 0)
        self.assertGreaterEqual(status["last_event_age_s"], 0)
        self.assertEqual(status["turns"][-1]["tools"], {"terminal": 1, "write_file": 1, "patch": 1})

    def test_send_keeps_the_sessions_turn_limit_unless_told_otherwise(self):
        self.install()
        self.run_cmd("start", "t1", "--brief", str(self.brief), "--cwd", str(self.project), "--max-turns", "120")
        self.wait_finished()
        self.assertEqual(self.run_cmd("send", "t1", "--brief", str(self.brief)).returncode, 0)
        self.wait_finished()
        first, second = self.calls()
        self.assertEqual(first[first.index("--max-turns") + 1], "120")
        self.assertNotIn("--max-turns", second)
        self.assertEqual(self.run_cmd("send", "t1", "--brief", str(self.brief), "--max-turns", "9").returncode, 0)
        self.wait_finished()
        third = self.calls()[2]
        self.assertEqual(third[third.index("--max-turns") + 1], "9")

    def test_start_has_a_default_turn_limit(self):
        self.install()
        self.start()
        self.wait_finished()
        args = self.calls()[0]
        self.assertEqual(args[args.index("--max-turns") + 1], "80")

    def test_report_prints_the_whole_final_text(self):
        self.install()
        self.start(env={"FAKE_MODE": "long_report"})
        self.assertNotIn("END-OF-REPORT", self.wait_finished().stdout)
        result = self.run_cmd("report", "t1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("END-OF-REPORT", result.stdout)

    def test_report_refuses_before_a_result_exists(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        result = self.run_cmd("report", "t1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no result", result.stderr)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def snapshot_project(self):
        self.git("init", "-q")
        (self.project / ".gitignore").write_text("*.ignored\n.hermes-delegate*\n")
        (self.project / "keep.txt").write_text("keep\n")
        (self.project / "change.txt").write_text("old\n")
        (self.project / "gone.txt").write_text("gone\n")
        self.git("add", "keep.txt", "change.txt", "gone.txt", ".gitignore")
        self.git("commit", "-q", "-m", "base")
        return self.git("rev-parse", "HEAD")

    def test_snapshot_copies_an_uncommitted_delivery(self):
        base = self.snapshot_project()
        self.install()
        self.start()
        self.wait_finished()
        (self.project / "change.txt").write_text("new\n")
        (self.project / "gone.txt").unlink()
        (self.project / "sub").mkdir()
        (self.project / "sub/added.bin").write_bytes(b"\x00\x01")
        (self.project / "junk.ignored").write_text("no\n")
        dest = self.root / "copy"
        result = self.run_cmd("snapshot", "t1", "--base", "HEAD", "--dest", str(dest))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((dest / "change.txt").read_text(), "new\n")
        self.assertEqual((dest / "keep.txt").read_text(), "keep\n")
        self.assertEqual((dest / "sub/added.bin").read_bytes(), b"\x00\x01")
        self.assertFalse((dest / "gone.txt").exists())
        self.assertFalse((dest / "junk.ignored").exists())
        summary = json.loads(result.stdout)
        self.assertEqual(summary["base"], base)
        self.assertEqual(summary["files"], 3)
        manifest = json.loads(Path(summary["manifest"]).read_text())
        by_path = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
        self.assertEqual(set(by_path), {"change.txt", "gone.txt", "sub/added.bin"})
        self.assertIsNone(by_path["gone.txt"])
        self.assertEqual(manifest["fingerprint"], summary["fingerprint"])

    def test_snapshot_fingerprint_changes_with_the_delivery(self):
        self.snapshot_project()
        self.install()
        self.start()
        self.wait_finished()
        (self.project / "change.txt").write_text("one\n")
        first = json.loads(self.run_cmd("snapshot", "t1", "--base", "HEAD").stdout)["fingerprint"]
        (self.project / "change.txt").write_text("two\n")
        second = json.loads(self.run_cmd("snapshot", "t1", "--base", "HEAD").stdout)["fingerprint"]
        self.assertNotEqual(first, second)

    def test_snapshot_refuses_while_the_turn_is_live(self):
        self.snapshot_project()
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        result = self.run_cmd("snapshot", "t1", "--base", "HEAD")
        self.assertEqual(result.returncode, 2)
        self.assertIn("running", result.stderr)

    def test_snapshot_refuses_while_the_session_runs_at_the_gateway(self):
        self.snapshot_project()
        self.install()
        self.start(env={"FAKE_MODE": "crash_gateway"})
        self.run_cmd("watch", "t1", "--timeout", "5")
        self.session_row("receiving stream response")
        result = self.run_cmd("snapshot", "t1", "--base", "HEAD")
        self.assertEqual(result.returncode, 2)
        self.assertIn("running", result.stderr)

    def other_session(self, description="tool: terminal", session="other-session"):
        self.session_row(description, session=session)

    def test_start_refuses_while_another_hermes_session_works(self):
        self.install()
        self.other_session()
        self.gateway_marker().touch()
        result = self.start()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("other-session", result.stderr)
        self.assertFalse(self.args.exists())

    def test_a_stale_activity_row_does_not_block_a_turn(self):
        self.install()
        self.other_session()
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()

    def test_an_unconfirmed_session_blocks_a_turn(self):
        self.install()
        self.other_session()
        result = self.start(env={"FAKE_HELPER": "broken"})
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("other-session", result.stderr)

    def test_simultaneous_starts_launch_only_one_turn(self):
        self.install()
        starts = [subprocess.Popen([sys.executable, str(DELEGATE), "start", f"r{n}", "--brief", str(self.brief),
                                    "--cwd", str(self.project)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env={**self.env, "FAKE_MODE": "hang"}) for n in range(6)]
        outcomes = [(start.communicate(timeout=20), start.returncode) for start in starts]
        self.assertEqual(sum(code == 0 for _, code in outcomes), 1, outcomes)
        self.assertEqual(len(list((self.root / "state").glob("*/state.json"))), 1)

    def test_sessions_lists_what_is_alive(self):
        self.install()
        self.start(env={"FAKE_MODE": "hang"})
        self.wait_for_event("tool_use")
        pid = json.loads((self.root / "state/t1/state.json").read_text())["pid"]
        watcher = subprocess.Popen([sys.executable, str(DELEGATE), "watch", "t1"], stdout=subprocess.DEVNULL, env=self.env)
        self.addCleanup(watcher.wait)
        self.addCleanup(watcher.kill)
        time.sleep(0.3)
        self.other_session()
        alive = json.loads(self.run_cmd("sessions").stdout)
        self.assertEqual([client["pid"] for client in alive["clients"]], [pid])
        self.assertEqual([entry["pid"] for entry in alive["watchers"]], [watcher.pid])
        self.assertEqual([session["id"] for session in alive["active_sessions"]], ["other-session"])

    def test_install_guard_upgrades_an_older_block(self):
        self.install()
        current = self.config.read_text()
        self.config.write_text(current.replace("|delegate_task", ""))
        self.assertNotEqual(self.config.read_text(), current)
        refused = self.start()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("install-guard", refused.stderr)
        self.install()
        self.assertEqual(self.config.read_text(), current)
        self.assertEqual(self.start().returncode, 0)

    def test_label_cannot_escape_state_dir(self):
        self.install()
        self.assertNotEqual(self.start("../evil").returncode, 0)


class SandboxTest(unittest.TestCase):
    """Inside a sandbox with its own PID namespace, a turn dies with the command and host processes are hidden."""

    def run_with_pid1(self, command_line, *args):
        sys.path.insert(0, str(HERE))
        self.addCleanup(sys.path.remove, str(HERE))
        import delegate
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HERMES_DELEGATE_STATE": tmp}):
            pid1 = Path(tmp) / "cmdline"
            pid1.write_bytes(command_line)
            error = io.StringIO()
            with mock.patch.object(delegate, "PID1_COMMAND", pid1), contextlib.redirect_stderr(error), \
                    contextlib.redirect_stdout(io.StringIO()):
                return delegate.main(list(args)), error.getvalue()

    def test_every_command_refuses_inside_codex_sandbox(self):
        for command_line in (b"codex-linux-sandbox\0--sandbox-policy-cwd\0/x\0", b"/usr/bin/bwrap\0--as-pid-1\0"):
            code, error = self.run_with_pid1(command_line, "status")
            self.assertEqual(code, 2)
            self.assertIn("outside", error)
            self.assertIn("require_escalated", error)

    def test_commands_run_on_the_host(self):
        for command_line in (b"/sbin/init\0", b"/lib/systemd/systemd\0", b"python3\0app.py\0"):
            self.assertEqual(self.run_with_pid1(command_line, "status")[0], 0)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.elsewhere = Path(self.tmp.name) / "elsewhere"
        self.project.mkdir()
        self.elsewhere.mkdir()
        (self.project / ".hermes-delegate.json").write_text(json.dumps({"guard": GUARD_RULES}))

    def decide(self, tool, tool_input, cwd=None):
        payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": tool_input,
                   "session_id": "s", "cwd": str(cwd or self.project), "profile": "default", "extra": {}}
        result = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def assert_blocked(self, tool, tool_input, cwd=None):
        decision = self.decide(tool, tool_input, cwd)
        self.assertEqual(decision.get("action"), "block", tool_input)
        self.assertTrue(decision.get("message"))

    def assert_allowed(self, tool, tool_input, cwd=None):
        self.assertEqual(self.decide(tool, tool_input, cwd), {}, tool_input)

    def test_blocks_forbidden_commands(self):
        for command in (
            "git push",
            "cd app && git push origin HEAD",
            "git -C app push -f",
            "git commit --no-verify -m x",
            "git reset --hard HEAD~1",
            "git clean -fdx",
            "git add -A",
            "git add .",
            "flashtool.py write_flash 0 a.bin",
            "python3 -m flashtool --chip esp32s3 read_mac",
            "fwtool -p /dev/ttyS0 flash",
            "FOO=1 fwtool erase-flash",
            "bash -c 'git push'",
            "docker stop model-worker",
            "docker compose down",
            "python3 scripts/svc_ctl.py stop",
            "cat config/prod.env",
            "cat data/.config.yaml",
            "timeout 60 git push",
            "git -c a=b push",
        ):
            self.assert_blocked("terminal", {"command": command})

    def test_allows_ordinary_work(self):
        for command in (
            "git status",
            "git log --oneline -3",
            "git add main/boards/mine/a.cc",
            "git commit -m 'feat: push notifications'",
            "grep -rn flashtool docs/",
            "echo git push",
            "rg 'fwtool flash' AGENTS.md",
            "python3 -m unittest discover -s app/scripts/tests",
            "make app PURPOSE='a test'",
            "docker ps",
            "python3 scripts/svc_ctl.py status",
            "cat docs/README.md",
            "cat config/prod.env.example",
            "git -C app add main/boards/mine/a.cc",
        ):
            self.assert_allowed("terminal", {"command": command})

    def test_write_tools_respect_protected_paths(self):
        for path in (
            "app/settings",
            "app/settings.defaults",
            "app/build/x.c",
            "app/main/boards/other/config.h",
            "config/prod.env",
            ".hermes-delegate.json",
        ):
            self.assert_blocked("write_file", {"path": path, "content": "x"})
            self.assert_blocked("patch", {"path": path})
        for path in ("app/main/boards/mine/a.cc", "app/main/app.cc"):
            self.assert_allowed("write_file", {"path": path, "content": "x"})

    def test_read_tool_blocks_secrets_only(self):
        self.assert_blocked("read_file", {"path": "config/prod.env"})
        self.assert_blocked("read_file", {"path": "data/.config.yaml"})
        self.assert_allowed("read_file", {"path": "app/settings"})

    def test_a_delegated_turn_cannot_start_subagents(self):
        self.assert_blocked("delegate_task", {"goal": "do part of it"})
        self.assert_allowed("delegate_task", {"goal": "x"}, cwd=self.elsewhere)

    def test_an_interactive_session_keeps_its_subagents(self):
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        db = sqlite3.connect(home / "state.db")
        db.execute("create table sessions (id text, cwd text, parent_session_id text, source text)")
        db.executemany("insert into sessions values (?, ?, null, ?)",
                       [("mine", str(self.project), "local"), ("run", str(self.project), "tool")])
        db.commit()
        db.close()
        def decide(session):
            payload = {"tool_name": "delegate_task", "tool_input": {"goal": "x"}, "session_id": session, "cwd": str(self.project)}
            return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True, text=True,
                                  timeout=10, env={**os.environ, "HERMES_HOME": str(home)}).stdout
        self.assertEqual(decide("mine"), "")
        self.assertIn("subagents", decide("run"))

    def test_execute_code_is_checked_by_text(self):
        self.assert_blocked("execute_code", {"code": "subprocess.run(['git', 'push'])"})
        self.assert_blocked("execute_code", {"code": "os.system('flashtool.py read_mac')"})
        self.assert_allowed("execute_code", {"code": "print('hello')"})

    def test_local_config_alone_marks_the_project_and_is_protected(self):
        (self.project / ".hermes-delegate.json").unlink()
        (self.project / ".hermes-delegate.local.json").write_text("{}")
        self.assert_blocked("terminal", {"command": "git push"})
        self.assert_blocked("write_file", {"path": ".hermes-delegate.local.json", "content": "x"})

    def test_local_guard_rules_apply_too(self):
        (self.project / ".hermes-delegate.local.json").write_text(
            json.dumps({"guard": {"blocked_commands": [{"pattern": "^mytool", "reason": "mine"}]}}))
        self.assert_blocked("terminal", {"command": "mytool run"})
        self.assert_blocked("terminal", {"command": "flashtool.py read_mac"})

    def test_builtin_rules_need_only_a_project_config(self):
        (self.project / ".hermes-delegate.json").write_text("{}")
        self.assert_blocked("terminal", {"command": "git push"})
        self.assert_allowed("terminal", {"command": "flashtool.py read_mac"})

    def test_only_applies_inside_a_configured_project(self):
        self.assert_allowed("terminal", {"command": "git push"}, cwd=self.elsewhere)
        self.assert_blocked("terminal", {"command": f"cd {self.project}/app && git push"}, cwd=self.elsewhere)
        self.assert_blocked("write_file", {"path": f"{self.project}/build/x"}, cwd=self.elsewhere)

    def session_call(self, command, session_id, rows, cwd=None):
        # A gateway-run session's payload names the gateway's directory; the store holds the session's.
        home = Path(self.tmp.name) / "home"
        home.mkdir(exist_ok=True)
        db = sqlite3.connect(home / "state.db")
        db.execute("create table if not exists sessions (id text, cwd text, parent_session_id text)")
        db.executemany("insert into sessions values (?, ?, ?)", rows)
        db.commit()
        db.close()
        payload = {"tool_name": "terminal", "tool_input": {"command": command}, "session_id": session_id,
                   "cwd": str(cwd or self.elsewhere)}
        return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=10, env={**os.environ, "HERMES_HOME": str(home)})

    def test_gateway_session_uses_the_stored_cwd(self):
        result = self.session_call("git push", "s1", [("s1", str(self.project), None)])
        self.assertEqual(json.loads(result.stdout).get("action"), "block")

    def test_subagent_inherits_its_parents_cwd(self):
        result = self.session_call("git push", "child", [("root", str(self.project), None), ("child", None, "root")])
        self.assertEqual(json.loads(result.stdout).get("action"), "block")

    def test_session_of_another_project_is_not_blocked(self):
        result = self.session_call("git push", "s2", [("s2", str(self.elsewhere), None)])
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_unreadable_session_store_blocks(self):
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        (home / "state.db").write_text("not a database, long enough to be read as a header" * 4)
        payload = {"tool_name": "terminal", "tool_input": {"command": "ls"}, "session_id": "s", "cwd": str(self.elsewhere)}
        result = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True,
                                text=True, timeout=10, env={**os.environ, "HERMES_HOME": str(home)})
        self.assertEqual(result.returncode, 2)

    def test_malformed_payload_blocks(self):
        result = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 2)

    def test_malformed_config_blocks(self):
        (self.project / ".hermes-delegate.json").write_text("not json")
        payload = json.dumps({"tool_name": "terminal", "tool_input": {"command": "ls"}, "cwd": str(self.project)})
        result = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
