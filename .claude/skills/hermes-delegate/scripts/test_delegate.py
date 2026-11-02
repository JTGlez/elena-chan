"""Tests for delegate.py and guard_hook.py. They use a fake hermes: no model, no GPU."""
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DELEGATE = HERE / "delegate.py"
GUARD = HERE / "guard_hook.py"

GUARD_RULES = {
    "blocked_commands": [
        {"pattern": "^flashtool", "reason": "flashing is the user's"},
        {"pattern": r"^python[0-9.]* .*\bflashtool\b", "reason": "flashing is the user's"},
        {"pattern": r"^fwtool .*\b(flash|erase[-_]flash)\b", "reason": "flashing is the user's"},
        {"pattern": r"^docker .*\b(stop|kill|rm|restart|down)\b", "reason": "other services"},
        {"pattern": r"^python[0-9.]* .*model_gate.*\b(stop|use)\b", "reason": "the gate"},
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
    with open(os.environ["FAKE_ARGS"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    mode = os.environ.get("FAKE_MODE", "ok")
    session = "sess-fake"
    def emit(**event):
        print(json.dumps({"timestamp": 1, **event}), flush=True)
    def interrupted(*_):
        emit(type="result", session_id=session, exit_code=130, text="", duration_ms=5,
             tokens={"input": 1, "output": 1, "total": 2})
        sys.exit(130)
    signal.signal(signal.SIGINT, interrupted)
    emit(type="system", subtype="init", model="fake", session_id=session)
    if mode == "crash":
        os._exit(3)
    emit(type="tool_use", name="terminal", input={"command": "git status"})
    emit(type="tool_result", name="terminal", output="BLOCKED: nope", duration_ms=4, is_error=True)
    emit(type="text", text="working")
    if mode == "hang":
        time.sleep(30)
    emit(type="result", session_id=session, exit_code=0, text="Done: abc123", duration_ms=7,
         tokens={"input": 10, "output": 20, "total": 30})
    """)

GATE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os
    print(json.dumps({"active": os.environ.get("FAKE_ACTIVE", "primary"),
                      "inflight": int(os.environ.get("FAKE_INFLIGHT", "0")), "switching": False}))
    """)


class DelegateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.fake = root / "hermes"
        self.fake.write_text(FAKE_HERMES)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.gate = root / "gate.py"
        self.gate.write_text(GATE)
        self.project = root / "project"
        self.project.mkdir()
        self.project_config = {"provider": "prov", "model": "mod-1",
                               "busy_check": {"command": [sys.executable, str(self.gate)],
                                              "expected_active": "primary"}}
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

    def test_refuses_when_model_is_busy(self):
        self.install()
        result = self.start(env={"FAKE_INFLIGHT": "2"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("inflight", result.stderr)
        self.assertFalse(self.args.exists())

    def test_refuses_when_other_model_is_active(self):
        self.install()
        result = self.start(env={"FAKE_ACTIVE": "secondary"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secondary", result.stderr)

    def test_runs_without_busy_check(self):
        self.install()
        del self.project_config["busy_check"]
        self.write_project_config()
        self.assertEqual(self.start().returncode, 0)
        self.wait_finished()

    def test_refuses_without_project_config(self):
        self.install()
        (self.project / ".hermes-delegate.json").unlink()
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".hermes-delegate.json", result.stderr)
        self.assertFalse(self.args.exists())

    def test_refuses_config_without_model(self):
        self.install()
        del self.project_config["model"]
        self.write_project_config()
        result = self.start()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("model", result.stderr)
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

    def test_label_cannot_escape_state_dir(self):
        self.install()
        self.assertNotEqual(self.start("../evil").returncode, 0)


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
            "python3 scripts/model_gate.py stop",
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
            "python3 scripts/model_gate.py status",
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

    def test_execute_code_is_checked_by_text(self):
        self.assert_blocked("execute_code", {"code": "subprocess.run(['git', 'push'])"})
        self.assert_blocked("execute_code", {"code": "os.system('flashtool.py read_mac')"})
        self.assert_allowed("execute_code", {"code": "print('hello')"})

    def test_builtin_rules_need_only_a_project_config(self):
        (self.project / ".hermes-delegate.json").write_text("{}")
        self.assert_blocked("terminal", {"command": "git push"})
        self.assert_allowed("terminal", {"command": "flashtool.py read_mac"})

    def test_only_applies_inside_a_configured_project(self):
        self.assert_allowed("terminal", {"command": "git push"}, cwd=self.elsewhere)
        self.assert_blocked("terminal", {"command": f"cd {self.project}/app && git push"}, cwd=self.elsewhere)
        self.assert_blocked("write_file", {"path": f"{self.project}/build/x"}, cwd=self.elsewhere)

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
