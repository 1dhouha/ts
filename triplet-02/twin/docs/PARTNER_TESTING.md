# Remote partner connection and test guide

Use this guide to connect the Brain and Guardian clients to a running Twin over
a private team network. For a cloud-hosted HTTPS address, follow the
[Cloud Deployment Guide](CLOUD_DEPLOYMENT.md) instead. The Twin owner runs the
server; partner teams run their own clients. Partners do not need Docker access
or the Twin's environment file.

## 1. Make the Twin reachable privately

1. Connect the Twin computer and partner computers to the same private network
   (the planned option is the team's Tailscale network).
2. On the Twin computer, get its reachable Tailscale IP or MagicDNS hostname.
   For an IPv4 address, run `tailscale ip -4`.
3. Add the exact address clients will put in the URL to
   `TWIN_ALLOWED_HOSTS` in `%USERPROFILE%\twin-layer.env`. For example:

   ```dotenv
   TWIN_ALLOWED_HOSTS=localhost,127.0.0.1,100.80.20.10
   ```

   Use the actual Tailscale address instead of the example. Hostnames and IPs
   are listed without a scheme, port, or path. If clients use a MagicDNS name,
   add that name too.
4. Restart Compose from the repository directory:

   ```powershell
   $env:TWIN_ENV_FILE = "$env:USERPROFILE\twin-layer.env"
   docker compose down
   docker compose up -d
   docker compose ps
   ```

5. From a partner computer, check
   `http://<twin-tailscale-ip>:8000/health/ready`. It should return JSON with
   `"status":"READY"`. If it cannot connect, check that both computers are
   online in the private network and that the Twin computer's firewall allows
   inbound TCP port 8000 over that network. Do not expose port 8000 publicly
   through router port forwarding.

The Compose service publishes port 8000 on the host. Tailscale carries traffic
between authorized team devices; keep access limited to the project members.

## 2. Give partners their own credentials

The Twin owner sends each team only its role token using the team's private
secret sharing method:

| Client | Token to provide | Role permissions |
| --- | --- | --- |
| Brain | `TWIN_TOKEN_BRAIN` | Read sensors/state/events; send actuator commands |
| Guardian | `TWIN_TOKEN_GUARDIAN` | Read sensors/state/events; isolate or restore devices |

Do not send `twin-layer.env`, another role's token, or tokens in source code or
group chat. Partners store their token in a local environment variable or
secret manager. Replacing a token in the Twin environment requires restarting
the service and securely distributing the replacement to that role.

The owner can open `%USERPROFILE%\twin-layer.env` locally and retrieve the
matching token for a one-to-one handoff. Send it only through the team's
approved private secret sharing method; do not paste it into this repository,
the project group chat, a screenshot, or a test report.

## 3. Run the connection checks

Set the base URL and the team's own token in that client's environment. Each
partner should create a private file outside their repository, such as
`%USERPROFILE%\brain-token.env`, containing only the token they received:

```dotenv
TWIN_TOKEN_BRAIN=<token provided privately to the Brain team>
```

For a PowerShell smoke check, load that local token without printing it:

```powershell
$env:TWIN_BASE_URL = "http://<twin-tailscale-ip>:8000"
$tokenLine = Get-Content "$env:USERPROFILE\brain-token.env" |
  Where-Object { $_ -like 'TWIN_TOKEN_BRAIN=*' }
$env:TWIN_TOKEN_BRAIN = $tokenLine.Substring('TWIN_TOKEN_BRAIN='.Length)

curl.exe --fail-with-body `
  -H "Authorization: Bearer $env:TWIN_TOKEN_BRAIN" `
  "$env:TWIN_BASE_URL/api/v1/sensors"
```

For the Guardian team, set `TWIN_TOKEN_GUARDIAN` from the Guardian's privately
provided token. Keep tokens out of command transcripts when sharing evidence.
The same read check can be made against `/api/v1/state`.

Expected checks:

| Check | Expected result |
| --- | --- |
| `GET /health/ready` | HTTP 200 and `READY`, no token required |
| `GET /api/v1/sensors` with that team's token | HTTP 200, `ok: true`, public readings only |
| `GET /api/v1/state` with that team's token | HTTP 200, `ok: true` |
| Same sensors request without a token | HTTP 401 |
| Brain attempts Guardian-only isolation | Denied; do not use it as a routine smoke test |

Never include `actual_value`, `delta`, or fault details in Brain/Guardian
evidence; those fields are private by design. The REST contract and command
schemas are documented in [the Interface Card](INTERFACE_CARD.md).

## 4. MCP client connection

For teams using MCP, configure their Streamable HTTP client with endpoint
`http://<twin-tailscale-ip>:8000/mcp/` and an HTTP header:

```text
Authorization: Bearer <that-client's-role-token>
```

Use the team's own MCP client library to set the endpoint and header. The REST
checks above are a simpler way to confirm network reachability and credentials
before debugging MCP client setup.

## 5. Handover evidence and cleanup

Ask each team to return the timestamp, role, endpoint path, HTTP status, and
sanitized response/error code for the checks they ran. They should not return
tokens or private sensor values. Record failures with the client library and
version plus the relevant server log excerpt, after checking it contains no
credentials.

To stop the Twin while retaining its JSONL log volume, run
`docker compose down` from the repository directory with `TWIN_ENV_FILE` set.
