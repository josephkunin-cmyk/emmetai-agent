# Codex Instructions — Emmet AI Deployment

## Quick Start
```bash
chmod +x CODEX_DEPLOY.sh
./CODEX_DEPLOY.sh
```

## What Needs to Happen

### 1. Push These Files to GitHub
The following files are ready to deploy:
- `app.py` — Main Flask app with admin API endpoints added
- `admin.html` — Admin dashboard (single-file, self-contained)
- `scraper.py` — Farming knowledge database
- `.gitignore` — Updated to prevent credential leaks
- `CODEX_DEPLOY.sh` — This deployment script

### 2. Set Environment Variables on Render
Go to https://dashboard.render.com → emmet-ai → Environment and ensure these are set:

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Claude API key | ✅ |
| `TWILIO_ACCOUNT_SID` | Twilio account SID | ✅ |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | ✅ |
| `TWILIO_PHONE_NUMBER` | +17179225968 | ✅ |
| `SQUARE_ACCESS_TOKEN` | Square API token | ✅ |
| `SQUARE_LOCATION_ID` | Square location ID | ✅ |
| `ADMIN_TOKEN` | Any strong password for dashboard access | ✅ |
| `DB_PATH` | ./hotline_usage.db | Optional |
| `FREE_DAILY_QUERIES` | 4 | Optional |

### 3. Verify Deployment
After push + Render auto-deploy:
```bash
# Check health
curl https://emmetai-agent.onrender.com/health

# Check dashboard loads
curl -s -o /dev/null -w "%{http_code}" https://emmetai-agent.onrender.com/admin/dashboard

# Test admin API
curl -H "X-Admin-Token: YOUR_TOKEN" https://emmetai-agent.onrender.com/admin/stats
```

### 4. Access the Dashboard
URL: `https://emmetai-agent.onrender.com/admin/dashboard`
Enter your ADMIN_TOKEN when prompted.

## Architecture
```
emmetai-agent/
├── app.py              # Flask backend (voice agent + admin APIs)
├── admin.html          # Admin dashboard (served at /admin/dashboard)
├── scraper.py          # Farming knowledge database
├── requirements.txt    # Python dependencies
├── .gitignore          # Credential protection
├── CODEX_DEPLOY.sh     # Deployment script
└── CODEX_INSTRUCTIONS.md  # This file
```

## Security Notes
- NEVER embed tokens in git remote URLs
- NEVER commit .env files
- ALL secrets go in Render Environment Variables only
- Admin dashboard requires X-Admin-Token header
- Rotate tokens every 90 days
- Revoke exposed tokens immediately at https://github.com/settings/tokens
