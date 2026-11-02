---
name: local-model
description: Use when setting up, choosing, starting, stopping or swapping LLMs served locally on an NVIDIA GPU, adding a model server, freeing the GPU, or pointing Hermes at a local model. Also for "cambia de modelo", "libera la GPU", "qué modelo está activo".
---

# Local models

A gate keeps one model server on the GPU. Each server keeps its own port and API key. A generation request to a port waits for running requests, stops the active server and starts the requested one, so clients only need a base URL and a key.

Everything specific to a machine lives in `config/gate.toml` and in each worker's folder, and neither is versioned.

## What you need

- Linux or WSL2 with an NVIDIA GPU and its driver.
- Docker with the Compose plugin and the NVIDIA Container Toolkit.
- Python 3.11 or newer.
- A systemd user session to run the gate as a service. Without one, run `model_gate.py serve` in a terminal.
- A worker for each model: a folder that serves it through Docker Compose. Requirements and a template: [references/worker-contract.md](references/worker-contract.md).

## Set up

Run from this skill's folder:

1. `python3 scripts/model_gate.py init` creates `config/gate.toml` from the example.
2. Create your workers under `.localworker/<name>/`, and add one `[[backend]]` block for each to `config/gate.toml`.
3. `python3 scripts/model_gate.py doctor` reports what is missing. Repeat until it shows no `[fail]`.
4. `python3 scripts/model_gate.py install-service --write`, then run the `systemctl` command it prints.

## Commands

| Task | Command |
|---|---|
| Check this machine and the config | `python3 scripts/model_gate.py doctor` |
| Which model is active | `python3 scripts/model_gate.py status` |
| Preload a model | `python3 scripts/model_gate.py use <backend>` |
| Free the GPU | `python3 scripts/model_gate.py stop` |
| Can a request run without disturbing anything | `python3 scripts/model_gate.py ready <backend>` |
| Gate logs | `journalctl --user -u model-gate -n 50` |
| Run its tests | `python3 -m unittest discover -s scripts -p 'test_model_gate.py'` |

After changing the gate's own code or `config/gate.toml`, run `systemctl --user restart model-gate` once `status` shows no running requests.

## Add or replace a model

1. Create or change the worker, following the contract.
2. Add or edit its `[[backend]]` block.
3. Run `doctor`, restart the gate, then run `use <backend>`.
4. Read the worker's logs with `docker compose logs` before calling it good.

## Use a model from Hermes

Hermes reaches a backend as a provider, like any remote API:

1. `python3 scripts/model_gate.py hermes-provider <backend>` prints the block for `~/.hermes/config.yaml` and the variable for `~/.hermes/.env`. Copy them there. The command never prints the key.
2. Start Hermes on it with `hermes --provider <provider> -m <model>`, or switch inside a session with `/model`.

The gate does the swap: the first request to another backend stops the active one, once its requests finish.

To delegate work with the `hermes-delegate` skill, give it the provider and model. For its `ready_command`, use `ready`, so a turn never stops a model that is in use:

```json
{
  "provider": "<provider>",
  "model": "<model>",
  "ready_command": ["python3", ".claude/skills/local-model/scripts/model_gate.py", "ready", "<backend>"]
}
```

## Who owns what

| Concern | Owner |
|---|---|
| Workers, the gate, which server is on the GPU, swaps | This skill |
| Providers, API keys for clients, which provider a session uses | Hermes configuration |
| Briefs, turns, supervision | `hermes-delegate`, which never needs to know a model is local |

## Rules

- Do not run `docker compose up` in a worker while the gate runs. The gate would not know about it, and two models may not fit on the GPU. Use `use` and `stop`.
- `use` and `stop` wait for running requests. If they answer that a request is still running, report it. Do not stop the container by hand, since that would cut off someone's request.
- After changing a worker's engine settings, apply them with `stop` and then `use`.
- Limits of one engine or model go in that worker's README, not here.
- Other GPU services are out of scope. Mention that they need VRAM too, but do not start or stop them unless asked.
