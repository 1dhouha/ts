# Local and Docker Deployment

## Local dashboard

From `triplet-07/twin`, install the locked Python requirements if needed, then run:

```powershell
python tools/run_dashboard.py
```

The launcher prompts for a one-launch operator passphrase and prints a local dashboard URL. It defaults to port 8765 and stores its SQLite state at `logs/twin-state.sqlite3`, unless `TWIN_STATE_DB_PATH` is set. Stop it with `quit` or Ctrl+C.

## ASGI service with Docker Compose

The Compose file requires `TWIN_ENV_FILE` to point to a private environment file. Start by copying `.env.example` to `.env`, replacing every placeholder with a strong unique value, and setting exact hostnames for the deployment. Then, from the Twin directory:

```powershell
$env:TWIN_ENV_FILE = ".env"
docker compose up --build
```

The service listens on container port 8000. The host port defaults to 8000 and can be changed with `TWIN_PUBLISH_PORT`. Persistent logs and the default SQLite state path are stored in the named `twin-layer-logs` volume.

The ASGI app refuses to start with missing, weak, repeated, or built-in development role tokens, and it requires `TWIN_ALLOWED_HOSTS`. Configure these values before starting. Keep `.env` out of version control and do not put credentials in command-line arguments, URLs, screenshots, or evidence artifacts.

## Image and runtime

The Dockerfile uses Python 3.12, installs hash-locked requirements, runs as an unprivileged `twin` user, and defines a readiness health check at `/health/ready`. Review `Dockerfile`, `docker-compose.yml`, `.env.example`, and `requirements.lock` for the deployed revision's exact settings.

## Limits

The local dashboard and Compose ASGI service are different launch paths. A working local demo does not prove that the Compose deployment is correctly configured, reachable, or integrated with partner services. Verify the selected path and save fresh evidence before a jury presentation.
