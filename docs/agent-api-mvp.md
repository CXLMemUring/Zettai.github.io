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

The current executor returns a structured Sandlock preview response. Replace that section in `handle_agent_run()` with the real RISC-V Sandlock worker when the runtime is ready.

## Local Run

```bash
cd /Users/yiweiyang/Downloads/Zettai.github.io
ZETTAI_STATIC_ROOT="$PWD" \
ZETTAI_DB=/tmp/zettai_agent_api.sqlite3 \
ZETTAI_PUBLIC_URL=http://127.0.0.1:8787 \
ZETTAI_FRONTEND_URL=http://127.0.0.1:8787/agent-api/ \
ZETTAI_DEV_LOGIN=1 \
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
  -d '{"image":"python:3.12-slim","command":"python -c \"print(2**32)\"","network":"openai"}'
```
