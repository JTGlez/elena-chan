#!/usr/bin/env python3
"""One GPU, several local LLM servers: each keeps its own port and only one runs at a time.

A generation request for a stopped backend waits for the running requests to finish, stops the
active backend, starts the requested one and is then forwarded. Standard library only.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_DIR / "config" / "gate.toml"
EXAMPLE_CONFIG = SKILL_DIR / "config" / "gate.example.toml"
SWITCHING_PATHS = {"/v1/messages", "/v1/chat/completions", "/v1/completions", "/v1/responses"}
COMMAND_TIMEOUT = 180.0
RETRY_AFTER_SECONDS = 30
MAX_HEADER_BYTES = 1 << 20
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 502: "Bad Gateway", 503: "Service Unavailable"}


@dataclass
class Backend:
    name: str
    listen_port: int
    upstream_port: int
    api_key: str
    start: list
    stop: list
    models: list
    key_var: str = ""
    hermes_provider: str = ""


class ConfigError(Exception):
    """The setup is incomplete; the message says what to do next."""


class GateError(Exception):
    def __init__(self, status: int, message: str, retry: bool = False):
        super().__init__(message)
        self.status, self.message, self.retry = status, message, retry


def log(message: str) -> None:
    print(f"[gate] {message}", flush=True)


def json_response(status: int, payload: dict, retry: bool = False) -> bytes:
    body = json.dumps(payload).encode()
    retry_line = f"Retry-After: {RETRY_AFTER_SECONDS}\r\n" if retry else ""
    head = (f"HTTP/1.1 {status} {REASONS[status]}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n{retry_line}Connection: close\r\n\r\n")
    return head.encode() + body


def error_response(status: int, message: str, retry: bool = False) -> bytes:
    return json_response(status, {"type": "error", "error": {"type": "gate_error", "message": message}}, retry)


def with_connection_close(head: bytes) -> bytes:
    """`head` with its Connection header replaced, so that one connection carries one request."""
    lines = [line for line in head[:-4].split(b"\r\n") if not line.lower().startswith(b"connection:")]
    return b"\r\n".join([*lines, b"Connection: close"]) + b"\r\n\r\n"


def key_of(head_lines: list[bytes]) -> str:
    for line in head_lines:
        name, _, value = line.partition(b":")
        name, value = name.strip().lower(), value.strip().decode("latin-1")
        if name == b"x-api-key":
            return value
        if name == b"authorization" and value.lower().startswith("bearer "):
            return value[7:].strip()
    return ""


class Gate:
    def __init__(self, backends, drain_timeout, load_timeout, poll_interval=0.5):
        self.backends = backends
        self.drain_timeout, self.load_timeout, self.poll_interval = drain_timeout, load_timeout, poll_interval
        self.active: Backend | None = None
        self.inflight = 0
        self.switching = False
        self.lock = asyncio.Lock()         # first come, first served: arrivals queue behind a running switch
        self.idle = asyncio.Event()
        self.idle.set()
        self.servers = []

    async def start(self) -> None:
        for backend in self.backends:
            if await self.healthy(backend):
                self.active = backend
                log(f"found {backend.name} already running")
                break
        for backend in self.backends:
            handler = lambda r, w, b=backend: self.handle(b, r, w)
            self.servers.append(await asyncio.start_server(handler, "127.0.0.1", backend.listen_port,
                                                           limit=MAX_HEADER_BYTES))

    async def healthy(self, backend: Backend) -> bool:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", backend.upstream_port)
        except OSError:
            return False
        try:
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), 3)
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        if b" 200 " not in head.split(b"\r\n", 1)[0]:
            return False
        try:
            state = json.loads(body)
        except ValueError:
            return True
        return bool(state.get("loaded", True)) and not state.get("loading", False)

    async def run(self, command: list) -> bool:
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL,
                                                       stderr=asyncio.subprocess.DEVNULL)
        try:
            return await asyncio.wait_for(process.wait(), COMMAND_TIMEOUT) == 0
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return False

    async def drain(self) -> None:
        try:
            await asyncio.wait_for(self.idle.wait(), self.drain_timeout)
        except asyncio.TimeoutError:
            raise GateError(503, f"{self.active.name} is still answering a request; try again", retry=True) from None

    async def stop_active(self) -> None:
        if self.active is None:
            return
        await self.drain()
        log(f"stopping {self.active.name}")
        if not await self.run(self.active.stop):
            raise GateError(503, f"could not stop {self.active.name}")
        self.active = None

    async def switch_to(self, backend: Backend) -> None:
        self.switching = True
        try:
            await self.stop_active()
            log(f"starting {backend.name}")
            if await self.run(backend.start) and await self.wait_healthy(backend):
                self.active = backend
                log(f"{backend.name} is ready")
                return
            await self.run(backend.stop)
            raise GateError(503, f"{backend.name} did not become ready", retry=True)
        finally:
            self.switching = False

    async def wait_healthy(self, backend: Backend) -> bool:
        deadline = asyncio.get_running_loop().time() + self.load_timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self.healthy(backend):
                return True
            await asyncio.sleep(self.poll_interval)
        return False

    def enter(self) -> None:
        self.inflight += 1
        self.idle.clear()

    def leave(self) -> None:
        self.inflight -= 1
        if self.inflight == 0:
            self.idle.set()

    async def acquire(self, backend: Backend) -> None:
        async with self.lock:
            if self.active is not backend:
                await self.switch_to(backend)
            self.enter()

    def status(self) -> dict:
        return {"active": self.active.name if self.active else None, "inflight": self.inflight,
                "switching": self.switching,
                "backends": [{"name": b.name, "port": b.listen_port, "models": b.models} for b in self.backends]}

    async def handle(self, backend: Backend, reader, writer) -> None:
        try:
            await self.serve_request(backend, reader, writer)
        except GateError as e:
            await self.send(writer, error_response(e.status, e.message, e.retry))
        except (OSError, EOFError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    @staticmethod
    async def send(writer, data: bytes) -> None:
        try:
            writer.write(data)
            await writer.drain()
        except OSError:
            pass

    async def serve_request(self, backend: Backend, reader, writer) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head[:-4].split(b"\r\n")
        try:
            method, target, _ = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            raise GateError(400, "malformed request") from None
        path = target.split("?", 1)[0]
        if path == "/gate/status":
            await self.send(writer, json_response(200, self.status()))
            return
        if path != "/health" and not hmac.compare_digest(key_of(lines[1:]).encode(), backend.api_key.encode()):
            raise GateError(401, "invalid API key")
        if path == "/gate/use" and method == "POST":
            await self.acquire(backend)
            self.leave()
            await self.send(writer, json_response(200, self.status()))
            return
        if path == "/gate/stop" and method == "POST":
            async with self.lock:
                await self.stop_active()
            await self.send(writer, json_response(200, self.status()))
            return
        if method == "POST" and path in SWITCHING_PATHS:
            await self.acquire(backend)
        elif self.active is backend and not self.switching:
            self.enter()
        elif method == "GET" and path == "/v1/models":
            models = [{"id": model, "object": "model"} for model in backend.models]
            await self.send(writer, json_response(200, {"object": "list", "data": models}))
            return
        else:
            raise GateError(503, f"{backend.name} is not running; a generation request starts it", retry=True)
        try:
            await self.forward(backend, head, reader, writer)
        finally:
            self.leave()

    async def forward(self, backend: Backend, head: bytes, reader, writer) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", backend.upstream_port,
                                                                 limit=MAX_HEADER_BYTES)
        except OSError:
            if self.active is backend and not self.switching:
                self.active = None
                log(f"{backend.name} stopped answering")
            raise GateError(502, f"{backend.name} is not answering; the next request restarts it") from None
        try:
            up_writer.write(with_connection_close(head))
            await up_writer.drain()
            to_upstream = asyncio.create_task(self.pipe(reader, up_writer))
            to_client = asyncio.create_task(self.pipe_response(up_reader, writer))
            await asyncio.wait({to_upstream, to_client}, return_when=asyncio.FIRST_COMPLETED)
            for task in (to_upstream, to_client):
                task.cancel()
            await asyncio.gather(to_upstream, to_client, return_exceptions=True)
        finally:
            up_writer.close()

    @staticmethod
    async def pipe(source, sink) -> None:
        while data := await source.read(1 << 16):
            sink.write(data)
            await sink.drain()

    async def pipe_response(self, source, sink) -> None:
        sink.write(with_connection_close(await source.readuntil(b"\r\n\r\n")))
        await sink.drain()
        await self.pipe(source, sink)


def read_config(path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"no config at {path}: run `model_gate.py init`, then edit it") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None


def read_env(path: pathlib.Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.lstrip().startswith("#"):
            values[name.strip()] = value.strip()
    return values


def worker_problem(config_path, entry: dict) -> str | None:
    """What keeps this worker from being used, or None. Never includes a value from its .env."""
    worker = (pathlib.Path(config_path).parent / entry["worker"]).resolve()
    if not (worker / "docker-compose.yml").is_file():
        return f"no docker-compose.yml in {worker}"
    if not (worker / ".env").is_file():
        return f"no .env in {worker}"
    if not read_env(worker / ".env").get(entry["key_var"]):
        return f"{entry['key_var']} is not set in {worker / '.env'}"
    return None


def load_config(path) -> dict:
    raw = read_config(path)
    backends = []
    for entry in raw["backend"]:
        problem = worker_problem(path, entry)
        if problem:
            raise ConfigError(f"backend {entry['name']}: {problem}")
        worker = (pathlib.Path(path).parent / entry["worker"]).resolve()
        compose = ["docker", "compose", "--project-directory", str(worker)]
        backends.append(Backend(name=entry["name"], listen_port=entry["listen_port"],
                                upstream_port=entry["upstream_port"],
                                api_key=read_env(worker / ".env")[entry["key_var"]],
                                start=[*compose, "up", "-d"], stop=[*compose, "down"], models=entry["models"],
                                key_var=entry["key_var"], hermes_provider=entry.get("hermes_provider", entry["name"])))
    return {"backends": backends, "drain_timeout": raw.get("drain_timeout", 300.0),
            "load_timeout": raw.get("load_timeout", 180.0)}


def not_ready_reason(status: dict, name: str) -> str | None:
    """Why a client of backend `name` should wait, or None. A stopped gate is reported by the caller."""
    if status["switching"]:
        return "the gate is switching models"
    if status["inflight"]:
        return f"{status['inflight']} request(s) running"
    if status["active"] not in (None, name):
        return f"{status['active']} is active, and a request to {name} would stop it"
    return None


def hermes_provider_block(backend: Backend) -> str:
    return f"""# In ~/.hermes/config.yaml, under providers:
providers:
  {backend.hermes_provider}:
    name: {backend.hermes_provider}
    base_url: http://127.0.0.1:{backend.listen_port}/v1
    api_mode: chat_completions
    default_model: {backend.models[0]}
    key_env: {backend.key_var}

# In ~/.hermes/.env, with the value from this worker's .env:
# {backend.key_var}=<value>
"""


def service_unit(config_path) -> str:
    python = shutil.which("python3") or sys.executable
    return f"""[Unit]
Description=Local LLM gate: one GPU, one running model

[Service]
ExecStart={python} {pathlib.Path(__file__).resolve()} --config {pathlib.Path(config_path).resolve()} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_service(config_path, write: bool) -> None:
    unit = service_unit(config_path)
    if not write:
        print(unit, end="")
        return
    home = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")
    target = home / "systemd" / "user" / "model-gate.service"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)  # may be a link to another checkout's unit
    target.write_text(unit)
    print(f"wrote {target}\nto start it: systemctl --user daemon-reload && systemctl --user enable --now model-gate")


def init_config(config_path) -> None:
    target = pathlib.Path(config_path)
    if target.exists():
        print(f"{target} already exists; left as it is")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXAMPLE_CONFIG, target)
    print(f"created {target}; edit it, then run `model_gate.py doctor`")


def command_output(*command) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def doctor(config_path) -> bool:
    """Report what this machine has and lacks. Starts nothing."""
    failed = False

    def report(level: str, message: str) -> None:
        nonlocal failed
        failed = failed or level == "fail"
        print(f"[{level}] {message}")

    gpus = command_output("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")
    if gpus:
        report("ok", f"nvidia-smi: {gpus.replace(chr(10), '; ')}")
    else:
        report("fail", "nvidia-smi: not found or failing; install the NVIDIA driver")
    if command_output("docker", "compose", "version"):
        report("ok", "docker compose")
    else:
        report("fail", "docker compose: not found; install Docker with the Compose plugin")
    runtimes = command_output("docker", "info", "--format", "{{json .Runtimes}}")
    if runtimes and "nvidia" in runtimes:
        report("ok", "docker: nvidia runtime")
    else:
        report("warn", "docker: no nvidia runtime listed; containers may not reach the GPU. "
                       "Install the NVIDIA Container Toolkit, unless you use CDI")
    try:
        raw = read_config(config_path)
    except ConfigError as e:
        report("fail", str(e))
        return not failed
    report("ok", f"config: {config_path}")
    home = pathlib.Path(os.environ.get("HERMES_HOME") or pathlib.Path.home() / ".hermes")
    hermes_config = home / "config.yaml"
    hermes_text = hermes_config.read_text() if hermes_config.is_file() else ""
    ports = [entry["listen_port"] for entry in raw["backend"]] + [entry["upstream_port"] for entry in raw["backend"]]
    if len(ports) != len(set(ports)):
        report("fail", "config: two ports are the same")
    for entry in raw["backend"]:
        problem = worker_problem(config_path, entry)
        report("fail", f"backend {entry['name']}: {problem}") if problem else report("ok", f"backend {entry['name']}")
        provider = entry.get("hermes_provider", entry["name"])
        if not re.search(rf"^\s+{re.escape(provider)}:", hermes_text, re.M):
            report("warn", f"Hermes has no provider {provider}: see `model_gate.py hermes-provider {entry['name']}`")
    if raw["backend"] and is_listening(raw["backend"][0]["listen_port"]):
        report("ok", "gate service: answering")
    else:
        report("warn", "gate service: not answering; see `model_gate.py install-service`")
    return not failed


def is_listening(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/gate/status", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def control(backend: Backend, path: str, method: str = "POST") -> dict:
    request = urllib.request.Request(f"http://127.0.0.1:{backend.listen_port}{path}", method=method,
                                     headers={"x-api-key": backend.api_key})
    try:
        with urllib.request.urlopen(request, timeout=COMMAND_TIMEOUT * 3) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        sys.exit(f"model-gate: {json.load(e)['error']['message']}")
    except urllib.error.URLError as e:
        sys.exit(f"model-gate: the service is not running ({e.reason})")


async def serve(config: dict) -> None:
    gate = Gate(config["backends"], config["drain_timeout"], config["load_timeout"])
    await gate.start()
    log("listening: " + ", ".join(f"{b.name}=127.0.0.1:{b.listen_port}" for b in gate.backends))
    await asyncio.Event().wait()


def run_command(args) -> None:
    if args.command == "init":
        init_config(args.config)
        return
    if args.command == "doctor":
        if not doctor(args.config):
            sys.exit(1)
        return
    if args.command == "install-service":
        install_service(args.config, args.write)
        return
    config = load_config(args.config)
    backends = {b.name: b for b in config["backends"]}
    first = config["backends"][0]
    if args.command == "serve":
        asyncio.run(serve(config))
    elif args.command == "status":
        print(json.dumps(control(first, "/gate/status", "GET"), indent=2))
    elif args.command == "stop":
        print(json.dumps(control(first, "/gate/stop"), indent=2))
    else:
        if args.name not in backends:
            sys.exit(f"model-gate: unknown backend {args.name}; choose from {', '.join(backends)}")
        if args.command == "use":
            print(json.dumps(control(backends[args.name], "/gate/use"), indent=2))
        elif args.command == "ready":
            reason = not_ready_reason(control(first, "/gate/status", "GET"), args.name)
            if reason:
                sys.exit(f"model-gate: {reason}")
            print("ready")
        else:
            print(hermes_provider_block(backends[args.name]), end="")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the config from the example")
    commands.add_parser("doctor", help="check this machine and the config; starts nothing")
    commands.add_parser("install-service", help="print the systemd user unit for this checkout").add_argument(
        "--write", action="store_true", help="write it to the user's systemd folder")
    commands.add_parser("serve", help="run the gate")
    commands.add_parser("status", help="show the active backend")
    commands.add_parser("stop", help="stop the active backend and free the GPU")
    commands.add_parser("use", help="start a backend ahead of a request").add_argument("name")
    commands.add_parser("ready", help="exit 0 if a request to this backend disturbs nothing").add_argument("name")
    commands.add_parser("hermes-provider", help="print the Hermes provider block for a backend").add_argument("name")
    args = parser.parse_args()
    try:
        run_command(args)
    except ConfigError as e:
        sys.exit(f"model-gate: {e}")


if __name__ == "__main__":
    main()
