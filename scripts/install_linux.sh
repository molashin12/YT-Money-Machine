#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# YouTube Shorts Generator — Linux Deployment Script
# ══════════════════════════════════════════════════════════════════════
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/your-repo/install.sh | bash
#   OR locally:
#   chmod +x scripts/install_linux.sh && sudo ./scripts/install_linux.sh
#
# What this does:
#   1. Installs system dependencies (Python 3, FFmpeg, Nginx, Certbot)
#   2. Creates a Python virtual environment and installs pip packages
#   3. Configures .env with your domain
#   4. Sets up Nginx reverse proxy with SSL (Let's Encrypt)
#   5. Creates a systemd service for auto-start on boot
#   6. Starts the application
#
# After running, your app will be accessible at https://your-domain.com
# ══════════════════════════════════════════════════════════════════════

set -e

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
header()  { echo -e "\n${PURPLE}══════════════════════════════════════${NC}"; echo -e "${PURPLE}  $1${NC}"; echo -e "${PURPLE}══════════════════════════════════════${NC}\n"; }

# ── Must be root ──
if [ "$EUID" -ne 0 ]; then
    error "Please run as root: sudo ./scripts/install_linux.sh"
fi

header "YouTube Shorts Generator — Installer"

# ── Detect OS ──
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    OS_VERSION=$VERSION_ID
else
    error "Cannot detect OS. Only Ubuntu/Debian are supported."
fi

info "Detected OS: $OS $OS_VERSION"

if [[ "$OS" != "ubuntu" && "$OS" != "debian" ]]; then
    warn "This script is optimized for Ubuntu/Debian. Other distros may work but are not tested."
fi

# ── Get user inputs ──
header "Configuration"

# Domain name
read -p "Enter your domain name (e.g. shorts.yourdomain.com): " DOMAIN
if [ -z "$DOMAIN" ]; then
    error "Domain name is required!"
fi

# Email for SSL
read -p "Enter email for SSL certificate (Let's Encrypt): " SSL_EMAIL
if [ -z "$SSL_EMAIL" ]; then
    error "Email is required for SSL!"
fi

# App user
APP_USER="ytshorts"
APP_DIR="/opt/youtube_automation"
VENV_DIR="$APP_DIR/venv"

info "Domain:    $DOMAIN"
info "SSL Email: $SSL_EMAIL"
info "App Dir:   $APP_DIR"
info "App User:  $APP_USER"
echo ""
read -p "Continue? (y/N): " CONFIRM
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
    echo "Aborted."
    exit 0
fi

# ══════════════════════════════════════════════════════════════════════
#  STEP 1: System Dependencies
# ══════════════════════════════════════════════════════════════════════
header "Step 1/6: Installing System Dependencies"

apt-get update -qq

# Python Setup
info "Installing latest Python 3..."
apt-get install -y software-properties-common
add-apt-repository ppa:deadsnakes/ppa -y
apt-get update -qq

# Install latest Python 3.13 and venv
apt-get install -y python3.13 python3.13-venv python3.13-dev python3-pip

# Map python3 to python3.13 if necessary for venv creation below
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1

# FFmpeg
if command -v ffmpeg &>/dev/null; then
    success "FFmpeg found"
else
    info "Installing FFmpeg..."
    apt-get install -y ffmpeg
    success "FFmpeg installed"
fi

# Nginx
if command -v nginx &>/dev/null; then
    success "Nginx found"
else
    info "Installing Nginx..."
    apt-get install -y nginx
    success "Nginx installed"
fi

# Certbot
if command -v certbot &>/dev/null; then
    success "Certbot found"
else
    info "Installing Certbot..."
    apt-get install -y certbot python3-certbot-nginx
    success "Certbot installed"
fi

# Git (for cloning if needed)
apt-get install -y git curl -qq 2>/dev/null || true

success "All system dependencies installed"

# ══════════════════════════════════════════════════════════════════════
#  STEP 2: Create App User & Copy Project
# ══════════════════════════════════════════════════════════════════════
header "Step 2/6: Setting Up Application"

# Create user if not exists
if id "$APP_USER" &>/dev/null; then
    info "User $APP_USER already exists"
else
    useradd -r -m -s /bin/bash "$APP_USER"
    success "Created user: $APP_USER"
fi

# Copy project files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -d "$APP_DIR" ]; then
    warn "Directory $APP_DIR already exists — updating files"
    rsync -a --exclude='venv' --exclude='data' --exclude='.env' --exclude='output' \
          "$SCRIPT_DIR/" "$APP_DIR/"
else
    mkdir -p "$APP_DIR"
    cp -r "$SCRIPT_DIR/"* "$APP_DIR/"
    cp "$SCRIPT_DIR/.env.example" "$APP_DIR/.env.example" 2>/dev/null || true
fi

# Create necessary directories
mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/output"
mkdir -p "$APP_DIR/assets/fonts"
mkdir -p "$APP_DIR/assets/music"

success "Project files copied to $APP_DIR"

# ══════════════════════════════════════════════════════════════════════
#  STEP 3: Python Virtual Environment & Dependencies
# ══════════════════════════════════════════════════════════════════════
header "Step 3/6: Installing Python Dependencies"

if [ -d "$VENV_DIR" ]; then
    info "Virtual environment exists — upgrading packages"
else
    info "Creating virtual environment..."
    python3.13 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" -q

success "Python dependencies installed"

# ══════════════════════════════════════════════════════════════════════
#  STEP 4: Configure Environment
# ══════════════════════════════════════════════════════════════════════
header "Step 4/6: Configuring Environment"

ENV_FILE="$APP_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    # Update BASE_URL and BOT_MODE in existing .env
    sed -i "s|^BASE_URL=.*|BASE_URL=https://$DOMAIN|" "$ENV_FILE"
    sed -i "s|^BOT_MODE=.*|BOT_MODE=webhook|" "$ENV_FILE"
    info "Updated existing .env"
else
    cat > "$ENV_FILE" <<EOF
# YouTube Shorts Generator — Production Config
BASE_URL=https://$DOMAIN
BOT_MODE=webhook
EOF
    info "Created new .env"
fi

success "Environment configured: BASE_URL=https://$DOMAIN, BOT_MODE=webhook"

# Set ownership
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ══════════════════════════════════════════════════════════════════════
#  STEP 5: Nginx + SSL
# ══════════════════════════════════════════════════════════════════════
header "Step 5/6: Configuring Nginx + SSL"

NGINX_CONF="/etc/nginx/sites-available/youtube-shorts"

# Step 5a: Create initial HTTP-only config (needed for Certbot challenge)
cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Increase max upload size for video files
    client_max_body_size 100M;
}
EOF

# Enable site
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

# Test and reload Nginx
nginx -t
systemctl reload nginx

success "Nginx configured for $DOMAIN"

# Step 5b: Obtain SSL certificate
info "Obtaining SSL certificate from Let's Encrypt..."
certbot --nginx -d "$DOMAIN" --email "$SSL_EMAIL" --agree-tos --non-interactive --redirect

success "SSL certificate obtained and configured!"
info "YouTube OAuth callback URL: https://$DOMAIN/api/youtube/callback"

# ══════════════════════════════════════════════════════════════════════
#  STEP 6: Systemd Service
# ══════════════════════════════════════════════════════════════════════
header "Step 6/6: Creating Systemd Service"

SERVICE_FILE="/etc/systemd/system/youtube-shorts.service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=YouTube Shorts Generator
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable youtube-shorts
systemctl restart youtube-shorts

success "Systemd service created and started"

# ══════════════════════════════════════════════════════════════════════
#  DONE!
# ══════════════════════════════════════════════════════════════════════
header "Installation Complete! 🎉"

echo -e "${GREEN}Your YouTube Shorts Generator is now running!${NC}"
echo ""
echo -e "  🌐 Web UI:           ${BLUE}https://$DOMAIN${NC}"
echo -e "  ⚙️  Admin Dashboard:  ${BLUE}https://$DOMAIN/admin${NC}"
echo -e "  🔐 YouTube Callback: ${BLUE}https://$DOMAIN/api/youtube/callback${NC}"
echo ""
echo -e "  📂 App Directory:    $APP_DIR"
echo -e "  📋 Logs:             ${YELLOW}journalctl -u youtube-shorts -f${NC}"
echo -e "  🔄 Restart:          ${YELLOW}systemctl restart youtube-shorts${NC}"
echo -e "  ⏹  Stop:             ${YELLOW}systemctl stop youtube-shorts${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Go to https://$DOMAIN/admin"
echo "  2. Add your API keys (Gemini, Pexels, Telegram Bot Token)"
echo "  3. Create your channels"
echo "  4. For YouTube upload: add OAuth creds and set redirect URI to:"
echo "     https://$DOMAIN/api/youtube/callback"
echo "  5. Add team members and set up cron jobs"
echo ""
