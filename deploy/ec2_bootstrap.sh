#!/usr/bin/env bash
#
# ec2_bootstrap.sh — one-time provisioning for the PS-4.3 detector on a
# free-tier EC2 t3.micro (Amazon Linux 2023).
#
# What it does and why:
#   - Installs Docker + the Compose plugin (needed to run api + Langfuse v2).
#   - Creates a 2GB swap file and sets vm.swappiness=10 so the kernel keeps the
#     detector's hot fastembed memory resident on a 1GB box (see Section 8 of
#     the implementation plan) rather than thrashing under the two-service load.
#   - Clones the repo and writes a chmod-600 `.env` with every secret the
#     compose stack needs, INCLUDING the Langfuse init keys so first boot needs
#     no manual UI step (eliminates the key chicken-and-egg problem).
#
# Secrets are prompted for (or read from the environment for non-interactive
# runs); nothing is hardcoded. Run once as a sudo-capable user (ec2-user).
#
# Usage:
#   REPO_URL=https://github.com/<you>/<repo>.git ./deploy/ec2_bootstrap.sh
#   (or run interactively and paste values when prompted)

set -euo pipefail

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap][error] %s\n' "$*" >&2; exit 1; }

REPO_URL="${REPO_URL:-}"
APP_DIR="${APP_DIR:-/opt/adversarial-detector}"
COMPOSE_PLUGIN_VERSION="${COMPOSE_PLUGIN_VERSION:-v2.29.7}"

# ---------------------------------------------------------------------------
# 1. System packages: Docker + git
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker and git via dnf ..."
  sudo dnf install -y docker git
else
  log "Docker already installed; ensuring git is present ..."
  sudo dnf install -y git
fi

sudo systemctl enable --now docker
if ! id -nG "$USER" | grep -qw docker; then
  log "Adding $USER to the docker group (re-login required for non-sudo docker) ..."
  sudo usermod -aG docker "$USER"
fi

# ---------------------------------------------------------------------------
# 2. Docker Compose plugin (AL2023 does not ship it with the docker package)
# ---------------------------------------------------------------------------
if ! docker compose version >/dev/null 2>&1; then
  log "Installing Docker Compose plugin ${COMPOSE_PLUGIN_VERSION} ..."
  PLUGIN_DIR="/usr/libexec/docker/cli-plugins"
  sudo mkdir -p "$PLUGIN_DIR"
  ARCH="$(uname -m)"
  sudo curl -fsSL \
    "https://github.com/docker/compose/releases/download/${COMPOSE_PLUGIN_VERSION}/docker-compose-linux-${ARCH}" \
    -o "${PLUGIN_DIR}/docker-compose"
  sudo chmod +x "${PLUGIN_DIR}/docker-compose"
fi
log "Compose version: $(sudo docker compose version 2>/dev/null || echo 'unavailable')"

# ---------------------------------------------------------------------------
# 3. Memory mitigations: 2GB swap + vm.swappiness=10
# ---------------------------------------------------------------------------
if ! sudo swapon --show | grep -q '/swapfile'; then
  log "Creating 2GB swap file ..."
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  if ! grep -q '/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
else
  log "Swap already active; skipping."
fi

log "Setting vm.swappiness=10 (persisted) ..."
sudo sysctl -w vm.swappiness=10 >/dev/null
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null

# ---------------------------------------------------------------------------
# 4. Clone (or update) the repository
# ---------------------------------------------------------------------------
if [[ -d "${APP_DIR}/.git" ]]; then
  log "Repo already present at ${APP_DIR}; pulling latest ..."
  sudo git -C "${APP_DIR}" pull --ff-only
else
  [[ -n "$REPO_URL" ]] || die "REPO_URL is not set and ${APP_DIR} has no clone. Re-run: REPO_URL=<git-url> $0"
  log "Cloning ${REPO_URL} into ${APP_DIR} ..."
  sudo mkdir -p "$APP_DIR"
  sudo chown "$USER:$USER" "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# ---------------------------------------------------------------------------
# 5. Write the chmod-600 .env (prompts unless value is already in the env)
# ---------------------------------------------------------------------------
prompt() {
  # prompt VAR "description" [secret]
  local var="$1" desc="$2" secret="${3:-}" current="${!1:-}" value
  if [[ -n "$current" ]]; then value="$current";
  elif [[ -n "$secret" ]]; then read -r -s -p "  ${desc}: " value; echo "";
  else read -r -p "  ${desc}: " value; fi
  printf '%s' "$value"
}

gen_secret() { openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | base64; }

if [[ -f .env ]]; then
  log ".env already exists at ${APP_DIR}/.env; leaving it untouched. Delete it to regenerate."
else
  log "Collecting configuration for .env (leave blank to accept generated/default) ..."
  EC2_PUBLIC_IP_IN="$(prompt EC2_PUBLIC_IP 'EC2 public IP (for Langfuse browser links)')"
  RDS_HOST_IN="$(prompt RDS_HOST 'RDS endpoint host')"
  RDS_PORT_IN="${RDS_PORT:-5432}"
  RDS_USER_IN="$(prompt RDS_USER 'RDS master username')"
  RDS_PASSWORD_IN="$(prompt RDS_PASSWORD 'RDS master password' secret)"
  ANTHROPIC_API_KEY_IN="$(prompt ANTHROPIC_API_KEY 'Anthropic API key' secret)"
  ADMIN_KEY_IN="$(prompt ADMIN_KEY 'Admin API key (X-Admin-Key)' secret)"
  LF_PUB_IN="${LANGFUSE_INIT_PROJECT_PUBLIC_KEY:-pk-lf-$(gen_secret | cut -c1-24)}"
  LF_SEC_IN="${LANGFUSE_INIT_PROJECT_SECRET_KEY:-sk-lf-$(gen_secret | cut -c1-24)}"
  NEXTAUTH_SECRET_IN="${NEXTAUTH_SECRET:-$(gen_secret)}"
  SALT_IN="${SALT:-$(gen_secret)}"

  DETECTOR_URL="postgresql://${RDS_USER_IN}:${RDS_PASSWORD_IN}@${RDS_HOST_IN}:${RDS_PORT_IN}/detector_db"
  LANGFUSE_URL="postgresql://${RDS_USER_IN}:${RDS_PASSWORD_IN}@${RDS_HOST_IN}:${RDS_PORT_IN}/langfuse_db"

  umask 077
  cat > .env <<ENV
# Generated by ec2_bootstrap.sh — chmod 600, never commit this file.
APP_ENV=production
LOG_LEVEL=INFO

# detector_db + langfuse_db on the SAME RDS instance
DATABASE_URL=${DETECTOR_URL}
LANGFUSE_DATABASE_URL=${LANGFUSE_URL}

# LLM (real Claude in production)
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY_IN}
LLM_MODEL=claude-3-5-haiku-latest

EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

# Admin guard
ADMIN_KEY=${ADMIN_KEY_IN}

# Langfuse v2 mirror (enabled on EC2 so the evaluator sees the UI)
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://${EC2_PUBLIC_IP_IN}:3000
LANGFUSE_INGEST_HOST=http://langfuse:3000
LANGFUSE_PUBLIC_KEY=${LF_PUB_IN}
LANGFUSE_SECRET_KEY=${LF_SEC_IN}
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${LF_PUB_IN}
LANGFUSE_INIT_PROJECT_SECRET_KEY=${LF_SEC_IN}
NEXTAUTH_URL=http://${EC2_PUBLIC_IP_IN}:3000
NEXTAUTH_SECRET=${NEXTAUTH_SECRET_IN}
SALT=${SALT_IN}
ENV
  chmod 600 .env
  log "Wrote ${APP_DIR}/.env (chmod 600)."
fi

log "Bootstrap complete."
log "Next: run  ./deploy/deploy.sh  (builds, migrates, then runs the post-deploy smoke gate)."
log "If you were just added to the docker group, log out and back in first."
