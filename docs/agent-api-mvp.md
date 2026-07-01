# Zettai Agent API MVP

This MVP adds a same-origin Agent API preview for `Zettai.github.io`.

## What Is Included

- Static page: `/agent-api/`
- Python stdlib backend: `api/zettai_agent_api.py`
- Lanxin launcher: `scripts/run_agent_api_lanxin.sh`
- Dev login, session cookie, API token creation, usage metering, and token balance
- Stripe Checkout wiring point
- GitHub and Google OAuth wiring points
- Preview endpoint: `POST /v1/agent/run`

The executor now calls a real `sandlock run` CLI when `ZETTAI_SANDLOCK_BIN` points to an executable binary. If the binary is missing, `/v1/agent/run` returns `executor_unavailable` and does not charge tokens.

## Local Run

```bash
cd /Users/yiweiyang/Downloads/Zettai.github.io
ZETTAI_STATIC_ROOT="$PWD" \
ZETTAI_DB=/tmp/zettai_agent_api.sqlite3 \
ZETTAI_PUBLIC_URL=http://127.0.0.1:8787 \
ZETTAI_FRONTEND_URL=http://127.0.0.1:8787/agent-api/ \
ZETTAI_DEV_LOGIN=1 \
ZETTAI_SANDLOCK_BIN=/path/to/sandlock \
python3 api/zettai_agent_api.py --host 127.0.0.1 --port 8787
```

Open:

```text
http://127.0.0.1:8787/agent-api/
```

## Lanxin Run

The current deployed path is:

```text
/mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/Zettai.github.io
```

Start:

```bash
ssh lanxin
cd /mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/Zettai.github.io
ZETTAI_PUBLIC_URL=http://172.16.80.19:8787 \
ZETTAI_FRONTEND_URL=http://172.16.80.19:8787/agent-api/ \
ZETTAI_SANDLOCK_BIN=/mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/bin/sandlock \
ZETTAI_DEV_LOGIN=1 \
scripts/run_agent_api_lanxin.sh
```

Health check:

```bash
curl http://127.0.0.1:8787/health
```

If the Lanxin IP is not reachable from your browser, tunnel it:

```bash
ssh -N -L 8787:127.0.0.1:8787 lanxin
```

Then open:

```text
http://127.0.0.1:8787/agent-api/
```

## Install Sandlock on Lanxin

The Lanxin image currently has Rust available through `~/.cargo/bin` on some boots, but that path may not be exported. To build Sandlock without filling `/`, use the NVMe helper:

```bash
ssh lanxin
cd /mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/Zettai.github.io
scripts/install_sandlock_lanxin.sh
```

Then restart the API with:

```bash
export ZETTAI_SANDLOCK_BIN=/mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/bin/sandlock
scripts/run_agent_api_lanxin.sh
```

## Stripe

Set these before starting the server:

```bash
export STRIPE_SECRET_KEY=sk_live_or_test_xxx
export STRIPE_PRICE_STARTER=price_xxx
export STRIPE_SUCCESS_URL=http://your-host/agent-api/?checkout=success
export STRIPE_CANCEL_URL=http://your-host/agent-api/?checkout=cancel
```

Without Stripe env vars, checkout adds preview tokens in dev mode.

## OAuth

GitHub:

```bash
export GITHUB_CLIENT_ID=...
export GITHUB_CLIENT_SECRET=...
```

Callback URL:

```text
http://your-host/auth/github/callback
```

Google:

```bash
export GOOGLE_CLIENT_ID=...
export GOOGLE_CLIENT_SECRET=...
```

Callback URL:

```text
http://your-host/auth/google/callback
```

## API Smoke

```bash
curl -c jar -b jar -X POST http://127.0.0.1:8787/api/dev-login \
  -H 'Content-Type: application/json' \
  -d '{"email":"founder@zett.ai"}'

TOKEN=$(curl -s -c jar -b jar -X POST http://127.0.0.1:8787/api/tokens \
  -H 'Content-Type: application/json' \
  -d '{"name":"default"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

curl -X POST http://127.0.0.1:8787/v1/agent/run \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"command":"python3 -c \"print(2**32)\"","network":"none","timeout":30}'
```

## Agent Run Request Schema

Useful fields for `POST /v1/agent/run`:

- `command`: string, executed as `/bin/sh -lc <command>`
- `argv`: array of strings, used instead of `command`
- `files`: object of relative path to text or `{ "encoding": "base64", "content": "..." }`
- `timeout`: seconds, capped by `ZETTAI_SANDBOX_MAX_TIMEOUT`
- `max_memory`: e.g. `"512M"`
- `max_processes`: process limit
- `network`: `none`, `openai`, `github`, or `all`
- `net_allow`: explicit Sandlock network rules, e.g. `["api.openai.com:443"]`
- `http_allow`: HTTP ACL rules, e.g. `["POST api.openai.com/v1/*"]`
- `fs_readable` / `fs_writable`: extra paths, in addition to the service defaults

The service adds a per-run writable workdir under `ZETTAI_SANDBOX_ROOT` and passes it to Sandlock as `--workdir`.
