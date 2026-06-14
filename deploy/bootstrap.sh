#!/usr/bin/env bash
# One-time server bootstrap for Avisa on a fresh Ubuntu 24.04 VPS (e.g. Hetzner CX22).
# Run as root, e.g.:
#   ssh root@SERVER_IP 'bash -s' < deploy/bootstrap.sh
# or copy it over and run `bash bootstrap.sh`.
#
# Idempotent: safe to re-run. Installs Docker, a host firewall (22/80/443),
# and clones the repo. It does NOT create .env or start the app — those are
# manual steps (see deploy/README.md) because they need your secrets/domain.
set -euo pipefail

# If the repo is private, either make it public, use a token in the URL, or
# rsync the directory up instead of cloning.
REPO_URL="${REPO_URL:-https://github.com/mhellevang/avisa.git}"
APP_DIR="${APP_DIR:-/opt/avisa}"

echo "→ Updating apt and installing Docker + compose plugin …"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io docker-compose-v2 git ufw
systemctl enable --now docker

echo "→ Configuring host firewall (allow 22/80/443, deny the rest) …"
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "→ Creating the 'deploy' user (the account GitHub Actions logs in as) …"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"

echo "→ Cloning/updating the repo into $APP_DIR (owned by $DEPLOY_USER) …"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$APP_DIR"

cat <<EOF

✓ Bootstrap done.

Next steps (see deploy/README.md):
  1. Add your CI deploy public key to /home/$DEPLOY_USER/.ssh/authorized_keys
     (so GitHub Actions can log in as $DEPLOY_USER).
  2. cd $APP_DIR
  3. cp .env.example .env  &&  edit .env
       - OPENROUTER_API_KEY, ADMIN_PASSWORD, SESSION_SECRET (required when public)
       - PAPER_TITLE / PAPER_LANG / PREFERENCES
  4. Edit Caddyfile  → set your real domain
  5. Add the GitHub repo secrets (DEPLOY_HOST/USER/SSH_KEY), then push to main.
     CI builds the image, pushes it to GHCR, and the server pulls + starts it.
     (Do NOT build here — the VPS only pulls.)
  6. Add the nightly backup cron (deploy/backup.sh)

After this, every push to main auto-deploys via .github/workflows/deploy.yml.
EOF
