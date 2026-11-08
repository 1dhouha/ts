# Cloud deployment for partner testing

This repository includes a Render Blueprint at the root (`render.yaml`). It
builds the existing Dockerfile as one Frankfurt web service, checks
`/health/ready`, configures the service's generated hostname as its allowed
host, and asks Render to generate distinct random values for all four role
tokens plus the dashboard operator passphrase. The Blueprint does not contain
credentials. The Person A city dashboard is served by the same application and
uses the same in-memory city owner as REST and MCP.

## Deploy

1. Push the repository and this Blueprint to a Git provider repository that
   your Render account can access. Do not add `twin-layer.env` or any real
   credentials to Git.
2. In Render, create a **Blueprint** from that repository and confirm the
   `cs-tsyp-twin-layer` web service. The Blueprint selects the free plan for
   temporary partner testing; review Render's current limits before creating
   it.
3. Wait for the Docker build and deploy to finish. The configured health check
   is `/health/ready`.
4. Copy the generated `https://<service>.onrender.com` URL from Render and
   verify `https://<service>.onrender.com/health/ready` returns `READY`.
5. In Render's service environment settings, retrieve and privately provide
   only `TWIN_TOKEN_BRAIN` to Brain and only `TWIN_TOKEN_GUARDIAN` to Guardian.
   Never share the other role credentials or put any token in a public issue,
   source file, or shared test report. Rotate a role's token after testing if
   its handling is uncertain.
6. Share `TWIN_DASHBOARD_SECRET` only with the city operator who needs to use
   the dashboard at the service root (`https://<service>.onrender.com/`). It is
   a separate credential from all Brain/Guardian tokens.

Render provides an HTTPS endpoint for the public web service. Dashboard
operator controls require the dashboard passphrase; REST and MCP still require
role Bearer tokens. `/docs` describes the REST API, and
`/api/v1/sensors` and `/api/v1/state` require authentication. Give each team
the HTTPS base URL and its own token through separate private channels.

## Partner smoke check

```bash
curl --fail-with-body https://<service>.onrender.com/health/ready
curl --fail-with-body \
  -H "Authorization: Bearer $TWIN_TOKEN_BRAIN" \
  https://<service>.onrender.com/api/v1/sensors
```

The first check is unauthenticated. For the second, each team uses its own
token in a local environment variable or secret manager. A request without a
token should receive HTTP 401. Keep token values out of terminal transcripts
and screenshots.

## Free service limits for this test deployment

Render's free web services spin down after 15 minutes without inbound traffic;
the first request after that can take about a minute while the service starts.
The free service has an ephemeral filesystem, so JSONL files written under
`/app/logs` are not durable. Restarts, spin-downs, and redeploys also reset the
in-memory simulation state, event buffer, quarantine, and idempotency cache.
Keep one service instance for a shared partner run so Brain and Guardian talk
to the same state. Free plan usage and bandwidth limits may change; see
[Render's current free service limits](https://render.com/docs/free) and
[pricing](https://render.com/pricing).

This is a public internet endpoint even though its API calls require role
tokens. Share the URL only with participants, use HTTPS, and rotate any token
that was exposed. The generated URL is permitted by the host allow-list; if a
custom domain is added later, add that exact domain to `TWIN_ALLOWED_HOSTS`.

## Cloud platform references

- [Render Docker deployment](https://render.com/docs/docker)
- [Render health checks](https://render.com/docs/health-checks)
- [Render environment variables and secrets](https://render.com/docs/configure-environment-variables)
- [Render Blueprint reference](https://render.com/docs/blueprint-spec)
