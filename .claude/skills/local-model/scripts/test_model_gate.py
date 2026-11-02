"""model_gate against real HTTP backends and real start/stop commands."""
import asyncio
import http.client
import http.server
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from model_gate import Backend, Gate, load_config, not_ready_reason  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeBackend:
    """Healthy only while its marker file exists, as a stopped container would not answer."""

    def __init__(self, name, marker):
        self.name, self.marker = name, marker
        self.requests, self.release, self.disconnected = [], threading.Event(), threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200 if outer.marker.exists() else 503, b'{"status":"ok"}')
                else:
                    outer.requests.append(self.path)
                    self._send(200, json.dumps({"from": outer.name, "path": self.path}).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append(self.path)
                if b"hold" in body:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b"first\n")
                    self.wfile.flush()
                    outer.release.wait(10)
                    try:
                        for _ in range(50):
                            self.wfile.write(b"more\n")
                            self.wfile.flush()
                            time.sleep(0.01)
                    except OSError:
                        outer.disconnected.set()
                    self.close_connection = True
                else:
                    self._send(200, json.dumps({"from": outer.name, "size": len(body)}).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


class GateCase(unittest.TestCase):
    drain_timeout = 5.0
    load_timeout = 3.0

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.log = self.dir / "commands.log"
        self.fakes, self.backends = {}, []
        for name in ("flash", "dense"):
            marker = self.dir / name
            fake = FakeBackend(name, marker)
            self.addCleanup(fake.release.set)
            self.addCleanup(fake.server.shutdown)
            self.fakes[name] = fake
            self.backends.append(Backend(
                name=name, listen_port=free_port(), upstream_port=fake.port, api_key=f"key-{name}",
                start=self.command(f"echo start {name} >> {self.log}; touch {marker}"),
                stop=self.command(f"echo stop {name} >> {self.log}; rm -f {marker}"),
                models=[f"model-{name}"]))
        self.flash, self.dense = self.backends

    @staticmethod
    def command(text):
        return ["sh", "-c", text]

    def serve(self, active=None):
        if active:
            (self.dir / active).touch()
        self.gate = Gate(self.backends, drain_timeout=self.drain_timeout, load_timeout=self.load_timeout,
                         poll_interval=0.02)
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.gate.start())
            ready.set()
            loop.run_forever()
            for server in self.gate.servers:
                server.close()
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        self.addCleanup(thread.join, 5)
        self.addCleanup(loop.call_soon_threadsafe, loop.stop)

    def commands(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def request(self, backend, method="POST", path="/v1/chat/completions", body=b"{}", key=None, timeout=15):
        conn = http.client.HTTPConnection("127.0.0.1", backend.listen_port, timeout=timeout)
        self.addCleanup(conn.close)
        headers = {"Authorization": f"Bearer {backend.api_key if key is None else key}"}
        conn.request(method, path, body=body if method == "POST" else None, headers=headers)
        return conn.getresponse()

    def call(self, backend, **kwargs):
        response = self.request(backend, **kwargs)
        return response.status, response.read()


class Routing(GateCase):
    def test_request_reaches_the_active_backend_without_commands(self):
        self.serve(active="flash")
        status, body = self.call(self.flash)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["from"], "flash")
        self.assertEqual(self.commands(), [])

    def test_request_for_the_inactive_backend_stops_the_active_one_first(self):
        self.serve(active="flash")
        status, body = self.call(self.dense)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["from"], "dense")
        self.assertEqual(self.commands(), ["stop flash", "start dense"])

    def test_anthropic_messages_also_switches(self):
        self.serve(active="flash")
        status, _ = self.call(self.dense, path="/v1/messages")
        self.assertEqual(status, 200)
        self.assertEqual(self.commands(), ["stop flash", "start dense"])

    def test_nothing_active_only_starts_the_target(self):
        self.serve()
        self.assertEqual(self.call(self.dense)[0], 200)
        self.assertEqual(self.commands(), ["start dense"])

    def test_large_body_arrives_whole(self):
        self.serve(active="flash")
        size = 3 * 1024 * 1024
        _, body = self.call(self.flash, body=b"x" * size)
        self.assertEqual(json.loads(body)["size"], size)

    def test_response_closes_the_connection_so_each_request_is_counted(self):
        self.serve(active="flash")
        response = self.request(self.flash)
        response.read()
        self.assertEqual(response.getheader("Connection"), "close")


class NoAccidentalSwitch(GateCase):
    def test_model_listing_of_inactive_backend_answers_without_switching(self):
        self.serve(active="flash")
        status, body = self.call(self.dense, method="GET", path="/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in json.loads(body)["data"]], ["model-dense"])
        self.assertEqual(self.commands(), [])

    def test_model_listing_of_active_backend_comes_from_it(self):
        self.serve(active="flash")
        _, body = self.call(self.flash, method="GET", path="/v1/models")
        self.assertEqual(json.loads(body)["from"], "flash")

    def test_token_count_on_inactive_backend_is_503_without_switching(self):
        self.serve(active="flash")
        status, _ = self.call(self.dense, path="/v1/messages/count_tokens")
        self.assertEqual(status, 503)
        self.assertEqual(self.commands(), [])

    def test_wrong_key_cannot_trigger_a_switch(self):
        self.serve(active="flash")
        status, _ = self.call(self.dense, key="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(self.commands(), [])

    def test_the_other_backends_key_is_refused(self):
        self.serve(active="flash")
        self.assertEqual(self.call(self.dense, key="key-flash")[0], 401)

    def test_x_api_key_header_is_accepted(self):
        self.serve(active="flash")
        conn = http.client.HTTPConnection("127.0.0.1", self.flash.listen_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", "/v1/messages", body=b"{}", headers={"x-api-key": "key-flash"})
        self.assertEqual(conn.getresponse().status, 200)


class Streaming(GateCase):
    def test_chunks_arrive_before_the_backend_finishes(self):
        self.serve(active="flash")
        response = self.request(self.flash, body=b"hold")
        self.assertEqual(response.read(6), b"first\n")
        self.assertFalse(self.fakes["flash"].release.is_set())
        self.fakes["flash"].release.set()
        self.assertTrue(response.read().startswith(b"more\n"))

    def test_client_leaving_closes_the_backend_stream(self):
        self.serve(active="flash")
        fake = self.fakes["flash"]
        conn = http.client.HTTPConnection("127.0.0.1", self.flash.listen_port, timeout=5)
        conn.request("POST", "/v1/chat/completions", body=b"hold", headers={"x-api-key": "key-flash"})
        response = conn.getresponse()
        response.read(6)
        response.close()
        conn.close()
        time.sleep(0.2)
        fake.release.set()
        self.assertTrue(fake.disconnected.wait(5))


class Draining(GateCase):
    def hold(self, backend):
        response = self.request(backend, body=b"hold")
        self.assertEqual(response.read(6), b"first\n")
        return response

    def test_switch_waits_for_the_running_request_to_finish(self):
        self.serve(active="flash")
        running = self.hold(self.flash)
        results = []
        waiter = threading.Thread(target=lambda: results.append(self.call(self.dense)[0]))
        waiter.start()
        time.sleep(0.3)
        self.assertEqual(self.commands(), [])
        self.fakes["flash"].release.set()
        rest = running.read()
        waiter.join(10)
        self.assertEqual(rest.count(b"more\n"), 50)
        self.assertEqual(results, [200])
        self.assertEqual(self.commands(), ["stop flash", "start dense"])

    def test_requests_arriving_during_a_switch_queue_behind_it(self):
        self.serve(active="flash")
        running = self.hold(self.flash)
        results = {}
        target = threading.Thread(target=lambda: results.update(dense=self.call(self.dense)[0]))
        target.start()
        time.sleep(0.2)
        later = threading.Thread(target=lambda: results.update(flash=json.loads(self.call(self.flash)[1])["from"]))
        later.start()
        time.sleep(0.2)
        self.assertNotIn("flash", results)
        self.fakes["flash"].release.set()
        running.read()
        target.join(10)
        later.join(10)
        self.assertEqual(results, {"dense": 200, "flash": "flash"})
        self.assertEqual(self.commands(), ["stop flash", "start dense", "stop dense", "start flash"])


class DrainTimeout(GateCase):
    drain_timeout = 0.3

    def test_a_request_that_outlasts_the_drain_gets_503_and_keeps_the_active_backend(self):
        self.serve(active="flash")
        running = self.request(self.flash, body=b"hold")
        running.read(6)
        response = self.request(self.dense)
        self.assertEqual(response.status, 503)
        self.assertIsNotNone(response.getheader("Retry-After"))
        self.assertEqual(self.commands(), [])
        self.fakes["flash"].release.set()
        self.assertEqual(running.read().count(b"more\n"), 50)


class Failures(GateCase):
    load_timeout = 0.3

    def test_failed_start_returns_503_and_a_later_request_recovers(self):
        self.serve(active="flash")
        working_start = self.dense.start
        self.dense.start = self.command("exit 3")
        self.assertEqual(self.call(self.dense)[0], 503)
        self.dense.start = working_start
        self.assertEqual(self.call(self.dense)[0], 200)

    def test_backend_that_never_gets_healthy_is_stopped_and_reported(self):
        self.serve()
        self.dense.start = self.command(f"echo start dense >> {self.log}")
        status, body = self.call(self.dense)
        self.assertEqual(status, 503)
        self.assertIn(b"error", body)
        self.assertEqual(self.commands(), ["start dense", "stop dense"])

    def test_failed_stop_does_not_start_the_target(self):
        self.serve(active="flash")
        self.flash.stop = self.command("exit 4")
        self.assertEqual(self.call(self.dense)[0], 503)
        self.assertEqual(self.commands(), [])

    def test_backend_that_died_is_restarted_by_the_next_request(self):
        self.serve(active="flash")
        self.fakes["flash"].server.shutdown()
        self.fakes["flash"].server.server_close()
        self.assertEqual(self.call(self.flash)[0], 502)
        self.assertEqual(self.gate.active, None)


class Control(GateCase):
    def status(self, backend):
        status, body = self.call(backend, method="GET", path="/gate/status", key="")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_status_reports_active_backend_without_a_key_and_without_secrets(self):
        self.serve(active="flash")
        result = self.status(self.dense)
        self.assertEqual(result["active"], "flash")
        self.assertEqual(result["inflight"], 0)
        self.assertNotIn(b"key-", json.dumps(result).encode())

    def test_use_switches_without_a_generation_request(self):
        self.serve(active="flash")
        self.assertEqual(self.call(self.dense, path="/gate/use")[0], 200)
        self.assertEqual(self.status(self.dense)["active"], "dense")
        self.assertEqual(self.fakes["dense"].requests, [])

    def test_stop_releases_the_active_backend(self):
        self.serve(active="flash")
        self.assertEqual(self.call(self.flash, path="/gate/stop")[0], 200)
        self.assertEqual(self.commands(), ["stop flash"])
        self.assertIsNone(self.status(self.flash)["active"])

    def test_control_requires_the_key(self):
        self.serve(active="flash")
        self.assertEqual(self.call(self.flash, path="/gate/stop", key="wrong")[0], 401)
        self.assertEqual(self.call(self.dense, path="/gate/use", key="wrong")[0], 401)
        self.assertEqual(self.commands(), [])

    def test_unknown_path_on_inactive_backend_is_503_without_switching(self):
        self.serve(active="flash")
        self.assertEqual(self.call(self.dense, method="GET", path="/")[0], 503)
        self.assertEqual(self.commands(), [])


class Config(unittest.TestCase):
    def test_a_worker_path_relative_to_the_config_gives_the_key_and_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            worker = root / "workers" / "one"
            worker.mkdir(parents=True)
            (worker / "docker-compose.yml").write_text("services: {}\n")
            (worker / ".env").write_text("# comment\nOTHER=1\nMY_KEY=abc=def\n")
            config_dir = root / "skill"
            config_dir.mkdir()
            (config_dir / "gate.toml").write_text("""
drain_timeout = 7
load_timeout = 9
[[backend]]
name = "one"
listen_port = 1
upstream_port = 2
worker = "../workers/one"
key_var = "MY_KEY"
models = ["m"]
""")
            config = load_config(config_dir / "gate.toml")
        self.assertEqual(config["drain_timeout"], 7)
        self.assertEqual(config["load_timeout"], 9)
        backend = config["backends"][0]
        self.assertEqual((backend.name, backend.api_key), ("one", "abc=def"))
        self.assertEqual(backend.start, ["docker", "compose", "--project-directory", str(worker), "up", "-d"])
        self.assertEqual(backend.stop, ["docker", "compose", "--project-directory", str(worker), "down"])


GATE_SCRIPT = pathlib.Path(__file__).with_name("model_gate.py")
KEY_VALUE = "not-a-real-key-value"


def run_gate(*args, env=None):
    return subprocess.run([sys.executable, str(GATE_SCRIPT), *args], capture_output=True, text=True, timeout=30,
                          env={**os.environ, **(env or {})})


class ProjectCase(unittest.TestCase):
    """A config with one worker folder, as a user would have after following the setup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.config = self.root / "config" / "gate.toml"
        self.config.parent.mkdir()
        self.worker = self.root / "workers" / "alpha"
        self.worker.mkdir(parents=True)
        (self.worker / "docker-compose.yml").write_text("services: {}\n")
        (self.worker / ".env").write_text(f"ALPHA_KEY={KEY_VALUE}\n")
        self.write_config()

    def write_config(self, extra=""):
        self.config.write_text(f"""
[[backend]]
name = "alpha"
listen_port = {free_port()}
upstream_port = {free_port()}
worker = "../workers/alpha"
key_var = "ALPHA_KEY"
models = ["alpha-model", "alpha-mini"]
{extra}
""")

    def gate(self, *args, env=None):
        return run_gate("--config", str(self.config), *args, env=env)


class Setup(ProjectCase):
    def test_init_copies_the_example_once(self):
        target = self.root / "new" / "gate.toml"
        first = run_gate("--config", str(target), "init")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("[[backend]]", target.read_text())
        target.write_text("# mine\n")
        again = run_gate("--config", str(target), "init")
        self.assertEqual(again.returncode, 0)
        self.assertEqual(target.read_text(), "# mine\n")

    def test_the_example_uses_placeholder_names(self):
        example = GATE_SCRIPT.parents[1] / "config" / "gate.example.toml"
        self.assertIn("example-model", example.read_text())
        self.assertIn("[[backend]]", example.read_text())

    def test_missing_config_says_how_to_create_it(self):
        result = run_gate("--config", str(self.root / "absent.toml"), "status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_worker_env_names_backend_and_file(self):
        (self.worker / ".env").unlink()
        result = self.gate("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alpha", result.stderr)
        self.assertIn(".env", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_key_names_the_variable(self):
        (self.worker / ".env").write_text("OTHER=1\n")
        result = self.gate("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ALPHA_KEY", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class HermesProvider(ProjectCase):
    def test_prints_a_provider_block_without_the_key_value(self):
        result = self.gate("hermes-provider", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        port = load_config(self.config)["backends"][0].listen_port
        self.assertIn("  alpha:", result.stdout)
        self.assertIn(f"base_url: http://127.0.0.1:{port}/v1", result.stdout)
        self.assertIn("default_model: alpha-model", result.stdout)
        self.assertIn("key_env: ALPHA_KEY", result.stdout)
        self.assertIn("ALPHA_KEY", result.stdout)
        self.assertNotIn(KEY_VALUE, result.stdout + result.stderr)

    def test_provider_name_can_differ_from_the_backend(self):
        self.write_config('hermes_provider = "engine-a"')
        self.assertIn("  engine-a:", self.gate("hermes-provider", "alpha").stdout)

    def test_unknown_backend_lists_the_choices(self):
        result = self.gate("hermes-provider", "beta")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alpha", result.stderr)


class Readiness(unittest.TestCase):
    def status(self, active="alpha", inflight=0, switching=False):
        return {"active": active, "inflight": inflight, "switching": switching,
                "backends": [{"name": "alpha"}, {"name": "beta"}]}

    def test_ready_when_the_backend_is_active_and_idle(self):
        self.assertIsNone(not_ready_reason(self.status(), "alpha"))

    def test_ready_when_nothing_is_loaded(self):
        self.assertIsNone(not_ready_reason(self.status(active=None), "alpha"))

    def test_not_ready_when_another_backend_would_be_stopped(self):
        self.assertIn("beta", not_ready_reason(self.status(active="beta"), "alpha"))

    def test_not_ready_during_requests_or_a_switch(self):
        self.assertIn("2", not_ready_reason(self.status(inflight=2), "alpha"))
        self.assertIn("switch", not_ready_reason(self.status(switching=True), "alpha"))

    def test_gate_down_is_not_ready(self):
        project = ProjectCase("write_config")
        project.setUp()
        self.addCleanup(project.tmp.cleanup)
        result = project.gate("ready", "alpha")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not running", result.stderr)


class Doctor(ProjectCase):
    def setUp(self):
        super().setUp()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.tool("docker", 'case "$1" in info) echo \'{"nvidia":{}}\';; *) echo ok;; esac')
        self.tool("nvidia-smi", "echo 'Some GPU, 24576 MiB'")
        self.home = self.root / "hermes"
        self.home.mkdir()
        (self.home / "config.yaml").write_text("providers:\n  alpha:\n    name: alpha\n")

    def tool(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def doctor(self):
        return self.gate("doctor", env={"PATH": f"{self.bin}:/usr/bin:/bin", "HERMES_HOME": str(self.home)})

    def test_complete_setup_passes_and_reports_the_gpu(self):
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Some GPU", result.stdout)
        self.assertNotIn("[fail]", result.stdout)

    def test_missing_gpu_tool_fails(self):
        (self.bin / "nvidia-smi").unlink()
        path_without = {"PATH": str(self.bin), "HERMES_HOME": str(self.home)}
        result = self.gate("doctor", env=path_without)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[fail] nvidia-smi", result.stdout)

    def test_missing_key_fails_without_printing_values(self):
        (self.worker / ".env").write_text("OTHER=secret-other\n")
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ALPHA_KEY", result.stdout)
        self.assertNotIn("secret-other", result.stdout)

    def test_missing_config_fails_with_the_next_step(self):
        self.config.unlink()
        result = self.doctor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init", result.stdout)

    def test_unknown_hermes_provider_is_only_a_warning(self):
        (self.home / "config.yaml").write_text("providers: {}\n")
        result = self.doctor()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("[warn]", result.stdout)
        self.assertIn("hermes-provider alpha", result.stdout)


class Service(ProjectCase):
    def test_prints_a_unit_with_this_checkout_paths(self):
        result = self.gate("install-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(GATE_SCRIPT), result.stdout)
        self.assertIn(str(self.config), result.stdout)
        self.assertNotIn("%h", result.stdout)

    def test_write_replaces_an_old_link_and_never_runs_systemctl(self):
        xdg = self.root / "xdg"
        unit = xdg / "systemd" / "user" / "model-gate.service"
        unit.parent.mkdir(parents=True)
        old = self.root / "old.service"
        old.write_text("old\n")
        unit.symlink_to(old)
        env = {"XDG_CONFIG_HOME": str(xdg), "PATH": "/nonexistent"}
        result = subprocess.run([sys.executable, str(GATE_SCRIPT), "--config", str(self.config), "install-service",
                                 "--write"], capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(unit.is_symlink())
        self.assertEqual(old.read_text(), "old\n")
        self.assertIn("ExecStart=", unit.read_text())
        self.assertIn("systemctl --user", result.stdout)



if __name__ == "__main__":
    unittest.main()
