#!/usr/bin/env bash
# ============================================================================
# CODEX DEPLOYMENT SCRIPT — Emmet AI
# ============================================================================
# This script handles secure deployment of the Emmet AI phone agent.
# Run this from the root of the emmetai-agent repository.
#
# Usage:
#   chmod +x CODEX_DEPLOY.sh
#   ./CODEX_DEPLOY.sh
#
# Prerequisites:
#   - Git installed and configured
#   - GitHub CLI (gh) installed and authenticated OR a GitHub token
#   - Access to the emmetai-agent repository
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Emmet AI — Secure Deployment Script${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"

# ── Step 1: Verify we're in the right repo ──────────────────────────────
echo -e "\n${YELLOW}[1/8] Verifying repository...${NC}"
if [ ! -f "app.py" ]; then
    echo -e "${RED}ERROR: app.py not found. Run this from the emmetai-agent repo root.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Repository verified${NC}"

# ── Step 2: Clean git remote (remove embedded tokens) ──────────────────
echo -e "\n${YELLOW}[2/8] Securing git remote URL...${NC}"
CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "none")
if echo "$CURRENT_REMOTE" | grep -q "ghp_"; then
    echo -e "${RED}⚠️  Found embedded token in git remote URL!${NC}"
    echo -e "${YELLOW}   Cleaning...${NC}"
    # Extract just the repo path
    REPO_PATH=$(echo "$CURRENT_REMOTE" | sed 's|.*github.com[:/]||' | sed 's|\.git$||')
    git remote set-url origin "https://github.com/${REPO_PATH}.git"
    echo -e "${GREEN}✅ Remote URL cleaned: https://github.com/${REPO_PATH}.git${NC}"
else
    echo -e "${GREEN}✅ Remote URL is clean${NC}"
fi

# ── Step 3: Check for exposed credentials in tracked files ─────────────
echo -e "\n${YELLOW}[3/8] Scanning for exposed credentials...${NC}"
CRED_FOUND=0
for pattern in "ghp_" "sk-" "EAAA" "sq0" "AC[a-f0-9]{32}" "SK[a-f0-9]{32}"; do
    if git grep -l "$pattern" -- '*.py' '*.html' '*.js' '*.json' '*.md' '*.txt' 2>/dev/null | grep -v ".gitignore" | grep -v "CODEX_DEPLOY.sh"; then
        echo -e "${RED}⚠️  Possible credential found matching pattern: $pattern${NC}"
        CRED_FOUND=1
    fi
done
if [ $CRED_FOUND -eq 0 ]; then
    echo -e "${GREEN}✅ No exposed credentials found in tracked files${NC}"
else
    echo -e "${RED}⚠️  Review the files above and remove credentials before deploying!${NC}"
    echo -e "${YELLOW}   Use environment variables on Render instead.${NC}"
fi

# ── Step 4: Verify .gitignore protects sensitive files ─────────────────
echo -e "\n${YELLOW}[4/8] Verifying .gitignore...${NC}"
GITIGNORE_OK=1
for pattern in ".env" "*.key" "*.pem" "credentials"; do
    if ! grep -q "$pattern" .gitignore 2>/dev/null; then
        echo -e "${RED}   Missing from .gitignore: $pattern${NC}"
        GITIGNORE_OK=0
    fi
done
if [ $GITIGNORE_OK -eq 1 ]; then
    echo -e "${GREEN}✅ .gitignore is properly configured${NC}"
else
    echo -e "${YELLOW}   Updating .gitignore...${NC}"
    cat >> .gitignore << 'IGNORE'

# Auto-added by deploy script
.env
.env.local
*.key
*.pem
credentials.json
credentials.txt
*.db
*.sqlite
IGNORE
    git add .gitignore
    echo -e "${GREEN}✅ .gitignore updated${NC}"
fi

# ── Step 5: Syntax check ──────────────────────────────────────────────
echo -e "\n${YELLOW}[5/8] Running syntax checks...${NC}"
if python3 -m py_compile app.py 2>/dev/null; then
    echo -e "${GREEN}✅ app.py syntax OK${NC}"
else
    echo -e "${RED}❌ app.py has syntax errors! Fix before deploying.${NC}"
    exit 1
fi
if [ -f "scraper.py" ]; then
    if python3 -m py_compile scraper.py 2>/dev/null; then
        echo -e "${GREEN}✅ scraper.py syntax OK${NC}"
    else
        echo -e "${RED}❌ scraper.py has syntax errors!${NC}"
        exit 1
    fi
fi

# ── Step 6: Stage and commit ─────────────────────────────────────────
echo -e "\n${YELLOW}[6/8] Staging changes...${NC}"
git add -A
if git diff --cached --quiet; then
    echo -e "${GREEN}✅ No changes to commit (already up to date)${NC}"
else
    TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M UTC")
    git commit -m "Deploy: admin dashboard + secure endpoints [$TIMESTAMP]

- Added /admin/dashboard serving admin.html
- Added /admin/stats, /admin/customers, /admin/subscriptions
- Added /admin/payments, /admin/call-logs endpoints
- Secured git remote (removed embedded tokens)
- Updated .gitignore for credential protection

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
    echo -e "${GREEN}✅ Changes committed${NC}"
fi

# ── Step 7: Push to GitHub ───────────────────────────────────────────
echo -e "\n${YELLOW}[7/8] Pushing to GitHub...${NC}"
if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
    echo -e "${BLUE}   Using GitHub CLI authentication...${NC}"
    git push origin main
    echo -e "${GREEN}✅ Pushed via GitHub CLI${NC}"
else
    echo -e "${YELLOW}   GitHub CLI not available. Trying git push...${NC}"
    if git push origin main 2>/dev/null; then
        echo -e "${GREEN}✅ Pushed to GitHub${NC}"
    else
        echo -e "${RED}❌ Push failed. Authenticate with one of:${NC}"
        echo -e "   ${BLUE}Option A:${NC} gh auth login"
        echo -e "   ${BLUE}Option B:${NC} git push https://oauth2:<YOUR_TOKEN>@github.com/josephkunin-cmyk/emmetai-agent.git main"
        echo -e "   ${BLUE}Option C:${NC} Set up SSH key: ssh-keygen -t ed25519"
        exit 1
    fi
fi

# ── Step 8: Verify deployment ────────────────────────────────────────
echo -e "\n${YELLOW}[8/8] Verifying deployment...${NC}"
echo -e "${BLUE}   Waiting 60 seconds for Render to deploy...${NC}"
sleep 60

HEALTH=$(curl -s -o /dev/null -w "%{http_code}" https://emmetai-agent.onrender.com/health 2>/dev/null || echo "000")
if [ "$HEALTH" = "200" ]; then
    echo -e "${GREEN}✅ Service is LIVE and healthy!${NC}"
else
    echo -e "${YELLOW}⚠️  Service returned HTTP $HEALTH (may still be deploying)${NC}"
    echo -e "${YELLOW}   Check: https://dashboard.render.com${NC}"
fi

echo -e "\n${BLUE}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   Deployment Complete!${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "   ${BLUE}Dashboard:${NC}  https://emmetai-agent.onrender.com/admin/dashboard"
echo -e "   ${BLUE}Service:${NC}    https://emmetai-agent.onrender.com/health"
echo -e "   ${BLUE}Phone:${NC}      (717) 922-5968"
echo -e "   ${BLUE}GitHub:${NC}     https://github.com/josephkunin-cmyk/emmetai-agent"
echo -e "   ${BLUE}Render:${NC}     https://dashboard.render.com"
echo -e ""
echo -e "${YELLOW}   REMINDER: Set ADMIN_TOKEN in Render environment variables!${NC}"
echo -e "${YELLOW}   Go to: Render → emmet-ai → Environment → Add ADMIN_TOKEN${NC}"
echo ""
