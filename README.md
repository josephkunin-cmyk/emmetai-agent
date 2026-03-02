# Emmet AI Hotline

Production voice hotline service for Twilio + Render + Anthropic.

## One-command preflight

Run this before testing by phone:

```bash
python3 scripts/preflight.py --base-url https://emmetai-agent.onrender.com
```

You should see all `PASS` lines.

Strict production check example:

```bash
python3 scripts/preflight.py \
  --base-url https://emmetai-agent.onrender.com \
  --expect-free-daily-queries 5 \
  --expect-model claude-haiku-4-5-20251001 \
  --require-square
```

## Verify and auto-fix Twilio webhook

Export Twilio credentials in your shell:

```bash
export TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export TWILIO_AUTH_TOKEN=your_auth_token
export TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
```

Check webhook settings only:

```bash
python3 scripts/preflight.py \
  --base-url https://emmetai-agent.onrender.com \
  --check-twilio
```

If it reports mismatch, auto-fix Twilio webhook:

```bash
python3 scripts/preflight.py \
  --base-url https://emmetai-agent.onrender.com \
  --check-twilio \
  --apply-twilio-webhook
```

Expected Twilio configuration:
- Voice URL: `https://emmetai-agent.onrender.com/voice` (POST)
- Status Callback: `https://emmetai-agent.onrender.com/status` (POST)

## Required Render env vars

- `ANTHROPIC_API_KEY`
- `PYTHON_VERSION=3.11.10`

Optional:
- `FREE_DAILY_QUERIES` (default `5`)
- `PAID_DAILY_QUERIES` (default `4`)
- `ANTHROPIC_MODEL` (default `claude-haiku-4-5-20251001`)
- `BUSINESS_TIMEZONE` (default `America/New_York`)
- `DB_PATH` (default `./hotline_usage.db`)
- `VOICE_NAME` (default `Polly.Joanna`)
- `SERVICE_PHONE_DISPLAY` (for `/health` and ops visibility)
- `UPGRADE_MESSAGE` (spoken when limit is hit)
- `SERVICE_SCOPE_MESSAGE` (spoken for out-of-scope requests)
- `SERVICE_GREETING` (first greeting text)
- `TWILIO_VALIDATE_SIGNATURE` (`true` to enforce signed Twilio webhooks)
- `TWILIO_MESSAGING_FROM` (needed to SMS payment links)
- `SQUARE_ACCESS_TOKEN`
- `SQUARE_LOCATION_ID`
- `SQUARE_ENVIRONMENT` (`production` or `sandbox`)
- `SQUARE_API_VERSION`
- `SQUARE_CURRENCY` (default `USD`)
- `SQUARE_DAILY_UNLOCK_CENTS` (default `500`)
- `SQUARE_WEBHOOK_SIGNATURE_KEY` (recommended)
- `PUBLIC_BASE_URL` (used for webhook/payment redirects)
- `APP_VERSION` (optional deploy/version marker in `/health`)
- `PORT` (Render sets this automatically)

## Limits and guardrails behavior

- The service enforces a daily per-caller cap (`FREE_DAILY_QUERIES`, default 5).
- Caller identity is based on Twilio `From` phone number.
- The intro now asks for caller name before Q&A starts.
- After the limit is reached, Emmet generates a Square payment link and attempts to SMS it to the caller.
- Once Square confirms payment (`/square-webhook`), the caller is unlocked for paid access for the rest of that day.
- Guardrails include:
  - emergency escalation to 911/988 language,
  - refusal of harmful/illegal/explicit requests,
  - scope steering to agriculture/equestrian/homestead/rural practical topics.

## Square webhook URL

Configure this in Square Dashboard:

- Endpoint: `https://emmetai-agent.onrender.com/square-webhook`
- Events: `payment.created`, `payment.updated`

## Local run

```bash
pip install -r requirements.txt
python app.py
```
