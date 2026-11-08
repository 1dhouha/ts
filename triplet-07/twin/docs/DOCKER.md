# Docker

The production image runs the unified ASGI app from `src/twin_app.py`. REST and
MCP Streamable HTTP share its one `LiveCity`, one `TwinFacade`, and one
application lifespan. The container runs one Uvicorn worker, binds to
`0.0.0.0`, and uses the hosting platform's `PORT` value (default `8000`).

## Build

From the repository root:

```powershell
docker build -t twin-layer:local .
```

The image installs the SHA-256-verified runtime dependency set exported from
`pyproject.toml`, copies the runtime source and scenario data, runs as an
unprivileged user, and probes `/health/ready` for container health. The Python
base image is pinned by digest. The image has no built-in role tokens or
development-token setting.

When changing runtime dependencies, update both lock files from the repository
root with uv installed:

```powershell
uv lock --python 3.12
uv export --locked --no-dev --format requirements.txt --no-emit-project `
  --no-header --output-file requirements.lock
```

## Configure and run

Provide the required role tokens and allowed hostnames through your hosting
platform's secret/environment settings. Each role token must be distinct, at
least 32 characters, and contain at least 16 distinct characters. Generate
tokens with a cryptographically secure random source, such as
`secrets.token_urlsafe(32)`. Never put real tokens in the image, Dockerfile,
source control, or logs.

Required variables:

- `TWIN_TOKEN_BRAIN`
- `TWIN_TOKEN_GUARDIAN`
- `TWIN_TOKEN_OPERATOR`
- `TWIN_TOKEN_EVALUATOR`
- `TWIN_ALLOWED_HOSTS` — comma-separated exact hostnames, without schemes,
  ports, paths, or wildcard entries

Optional settings include `PORT`, `TWIN_ALLOWED_ORIGINS`, `TWIN_RUN_ID`,
`TWIN_LOG_DIR`, and `TWIN_TRUSTED_PROXY_IPS`. Configure the trusted proxy list
only with the actual ingress proxy addresses for the deployment. If the
platform terminates TLS, add the platform's public hostname to
`TWIN_ALLOWED_HOSTS`.

For a local run, create a private environment file outside the repository
(for example, `$env:USERPROFILE\twin-layer.env` in PowerShell), then start the
container:

```powershell
docker run --rm --name twin-layer `
  --env-file "$env:USERPROFILE\twin-layer.env" `
  --publish 8000:8000 `
  --volume twin-layer-logs:/app/logs `
  twin-layer:local
```

Set `TWIN_ALLOWED_HOSTS=localhost,127.0.0.1` in that local environment file.
The `.dockerignore` excludes `.env*` files from the build context. The named
volume preserves JSONL log files only; the simulation and event store remain
in memory and do not survive container replacement.

## Docker Compose

Compose builds the same image, publishes local port `8000`, restarts the
service unless stopped, and keeps JSONL logs in the `twin-layer-logs` named
volume. Point `TWIN_ENV_FILE` at the private environment file created above:

```powershell
$env:TWIN_ENV_FILE = "$env:USERPROFILE\twin-layer.env"
docker compose up --build
```

Open `http://localhost:8000/health/ready` to check readiness. Stop the service
with `Ctrl+C`; use `docker compose down` to remove the stopped service while
retaining the log volume. To remove the logs too, run
`docker compose down --volumes`. Set `TWIN_PUBLISH_PORT` to change the
host-side port, for example `$env:TWIN_PUBLISH_PORT = "8080"` publishes the
app at `http://localhost:8080`.

The container reports its readiness at `/health/ready` and liveness at
`/health/live`. REST and MCP both use the same `PORT`; MCP is mounted at
`/mcp/`. Uvicorn's graceful shutdown runs the application lifespan, which
stops the simulation and closes the scenario logger and MCP session manager.

For a public HTTPS endpoint for remote partner testing, see the
[cloud deployment guide](CLOUD_DEPLOYMENT.md) and the root `render.yaml`
Blueprint.
