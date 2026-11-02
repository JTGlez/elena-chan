# Worker contract

A worker is a folder that runs one model server through Docker Compose. Any engine works if the worker meets this contract. Workers are not versioned, because they hold API keys and model weights.

## Requirements

| The worker must | Because |
|---|---|
| Have `docker-compose.yml` at its root | The gate runs `docker compose up -d` and `down` there |
| Have `.env` holding the API key under the name in `key_var` | The gate checks client keys against it |
| Publish its server on `127.0.0.1:<upstream_port>` only | Clients must go through the gate |
| Answer `GET /health` with 200 once the model can serve | The gate waits for this after starting it |
| Serve the OpenAI API at `/v1/chat/completions` | Clients such as Hermes use it. The gate also routes `/v1/completions`, `/v1/responses` and `/v1/messages` |
| Accept the same API key from the `Authorization` or `x-api-key` header | The gate forwards the request's headers unchanged |
| Use `restart: "no"` | Only the gate decides what runs |

If `/health` returns JSON, the gate also waits while `loaded` is false or `loading` is true.

## Layout

```text
.localworker/<name>/
├── docker-compose.yml
├── .env               # API key and settings; never versioned
├── .env.example       # The same names, without values
├── README.md          # Model, engine, how to download, and that engine's limits
├── config/            # Engine settings
├── models/            # Weights
└── cache/
```

## Template

Replace the image and the server's command with your engine's. Set `<internal_port>` to the port it listens on inside the container, and `<upstream_port>` to the backend's value in `config/gate.toml`.

```yaml
name: my-worker

services:
  server:
    image: <your-engine-image>
    container_name: my-worker
    env_file: .env
    gpus: all
    restart: "no"
    ports:
      - "127.0.0.1:<upstream_port>:<internal_port>"
    volumes:
      - ./models:/models
      - ./config:/config:ro
      - ./cache:/cache
    stop_grace_period: 60s
```

Then add its block to `config/gate.toml`:

```toml
[[backend]]
name = "my-model"
listen_port = 8082
upstream_port = 18082
worker = "../../../../.localworker/my-worker"
key_var = "MY_WORKER_API_KEY"
models = ["my-model-id"]
```

## Choosing what fits

`doctor` prints the GPU and its memory. Weights, context size and engine reserve all count against that memory, and engines differ in how much they can keep in system RAM. Check the engine's documentation for your GPU's architecture before downloading weights.
