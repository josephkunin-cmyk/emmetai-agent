# Emmet AI Hotline

Production voice hotline service for Twilio + Render + Anthropic.

## One-command preflight

Run this before testing by phone:

```bash
python3 scripts/preflight.py --base-url https://emmetai-agent.onrender.com
```

You should see all `PASS` lines.

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
- `BUSINESS_TIMEZONE` (default `America/New_York`)
- `DB_PATH` (default `./hotline_usage.db`)
- `UPGRADE_MESSAGE` (spoken when limit is hit)
- `SERVICE_SCOPE_MESSAGE` (spoken for out-of-scope requests)
- `SERVICE_GREETING` (first greeting text)
- `PORT` (Render sets this automatically)

## Limits and guardrails behavior

- The service enforces a daily per-caller cap (`FREE_DAILY_QUERIES`, default 5).
- Caller identity is based on Twilio `From` phone number.
- After the limit is reached, Emmet stops answering and plays `UPGRADE_MESSAGE`.
- Guardrails include:
  - emergency escalation to 911/988 language,
  - refusal of harmful/illegal/explicit requests,
  - scope steering to agriculture/equestrian/homestead/rural practical topics.

## Local run

```bash
pip install -r requirements.txt
python app.py
```
