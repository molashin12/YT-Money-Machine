# ══════════════════════════════════════════════════════════════════════
# YouTube Shorts Generator — Windows Deployment Script
# ══════════════════════════════════════════════════════════════════════
#
# Usage (Run as Administrator):
#   .\scripts\install_windows.ps1
#
# What this does:
#   1. Checks/installs Python 3, FFmpeg (via winget), pip packages
#   2. Creates .env with your domain
#   3. Installs Caddy as reverse proxy with automatic SSL
#   4. Creates a Windows Task Scheduler entry for auto-start
#   5. Starts the application
#
# After running, your app will be accessible at https://your-domain.com
# ══════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"

# ── Helpers ──
function Write-Header($msg) {
    Write-Host ""
    Write-Host "══════════════════════════════════════" -ForegroundColor Magenta
    Write-Host "  $msg" -ForegroundColor Magenta
    Write-Host "══════════════════════════════════════" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info($msg)    { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Success($msg) { Write-Host "[✓] $msg" -ForegroundColor Green }
function Write-Warn($msg)    { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)     { Write-Host "[✗] $msg" -ForegroundColor Red; exit 1 }

# ── Must be admin ──
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "Please run PowerShell as Administrator!"
}

Write-Header "YouTube Shorts Generator — Installer"

# ── Get inputs ──
Write-Header "Configuration"

$DOMAIN = Read-Host "Enter your domain name (e.g. shorts.yourdomain.com)"
if ([string]::IsNullOrWhiteSpace($DOMAIN)) { Write-Err "Domain name is required!" }

$SSL_EMAIL = Read-Host "Enter email for SSL certificate"
if ([string]::IsNullOrWhiteSpace($SSL_EMAIL)) { Write-Err "Email is required!" }

$APP_DIR = "C:\youtube_automation"
Write-Info "Domain:  $DOMAIN"
Write-Info "App Dir: $APP_DIR"
Write-Host ""
$confirm = Read-Host "Continue? (y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y") { Write-Host "Aborted."; exit 0 }

# ══════════════════════════════════════════════════════════════════════
#  STEP 1: System Dependencies
# ══════════════════════════════════════════════════════════════════════
Write-Header "Step 1/6: System Dependencies"

# Check winget
$hasWinget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $hasWinget) {
    Write-Warn "winget not found. Attempting manual installs..."
}

# Python
$python = Get-Command python -ErrorAction SilentlyContinue
if ($python) {
    $pyVer = python --version 2>&1
    Write-Success "Python found: $pyVer"
} else {
    Write-Info "Installing Python 3..."
    if ($hasWinget) {
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    } else {
        Write-Err "Python 3 not found. Install from https://python.org and re-run."
    }
}

# FFmpeg
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Write-Success "FFmpeg found"
} else {
    Write-Info "Installing FFmpeg..."
    if ($hasWinget) {
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    } else {
        Write-Err "FFmpeg not found. Install from https://ffmpeg.org and re-run."
    }
}

# Caddy (reverse proxy with auto-SSL)
$caddy = Get-Command caddy -ErrorAction SilentlyContinue
if ($caddy) {
    Write-Success "Caddy found"
} else {
    Write-Info "Installing Caddy (reverse proxy with auto-SSL)..."
    if ($hasWinget) {
        winget install CaddyServer.Caddy --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    } else {
        Write-Warn "Caddy not found. Install from https://caddyserver.com/docs/install"
        Write-Warn "Continuing without Caddy — you'll need to set up SSL manually."
    }
}

Write-Success "System dependencies checked"

# ══════════════════════════════════════════════════════════════════════
#  STEP 2: Copy Project
# ══════════════════════════════════════════════════════════════════════
Write-Header "Step 2/6: Setting Up Application"

$SCRIPT_DIR = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)

if (Test-Path $APP_DIR) {
    Write-Warn "Directory $APP_DIR exists — updating files"
    # Copy new files but preserve data, .env, venv, output
    Get-ChildItem $SCRIPT_DIR -Exclude "venv","data",".env","output" | ForEach-Object {
        Copy-Item $_.FullName -Destination $APP_DIR -Recurse -Force
    }
} else {
    Copy-Item $SCRIPT_DIR -Destination $APP_DIR -Recurse
}

# Create directories
New-Item -ItemType Directory -Force -Path "$APP_DIR\data" | Out-Null
New-Item -ItemType Directory -Force -Path "$APP_DIR\output" | Out-Null
New-Item -ItemType Directory -Force -Path "$APP_DIR\assets\fonts" | Out-Null
New-Item -ItemType Directory -Force -Path "$APP_DIR\assets\music" | Out-Null

Write-Success "Project copied to $APP_DIR"

# ══════════════════════════════════════════════════════════════════════
#  STEP 3: Python Virtual Environment
# ══════════════════════════════════════════════════════════════════════
Write-Header "Step 3/6: Python Dependencies"

$VENV_DIR = "$APP_DIR\venv"
$VENV_PIP = "$VENV_DIR\Scripts\pip.exe"
$VENV_PYTHON = "$VENV_DIR\Scripts\python.exe"
$VENV_UVICORN = "$VENV_DIR\Scripts\uvicorn.exe"

if (-not (Test-Path $VENV_DIR)) {
    Write-Info "Creating virtual environment..."
    python -m venv $VENV_DIR
}

& $VENV_PIP install --upgrade pip -q
& $VENV_PIP install -r "$APP_DIR\requirements.txt" -q

Write-Success "Python dependencies installed"

# ══════════════════════════════════════════════════════════════════════
#  STEP 4: Configure Environment
# ══════════════════════════════════════════════════════════════════════
Write-Header "Step 4/6: Configuring Environment"

$ENV_FILE = "$APP_DIR\.env"

if (Test-Path $ENV_FILE) {
    (Get-Content $ENV_FILE) -replace "^BASE_URL=.*", "BASE_URL=https://$DOMAIN" `
                             -replace "^BOT_MODE=.*", "BOT_MODE=webhook" |
        Set-Content $ENV_FILE
    Write-Info "Updated existing .env"
} else {
    @"
# YouTube Shorts Generator — Production Config
BASE_URL=https://$DOMAIN
BOT_MODE=webhook
"@ | Set-Content $ENV_FILE
    Write-Info "Created new .env"
}

Write-Success "Environment configured: BASE_URL=https://$DOMAIN"

# ══════════════════════════════════════════════════════════════════════
#  STEP 5: Caddy (Reverse Proxy + Auto-SSL)
# ══════════════════════════════════════════════════════════════════════
Write-Header "Step 5/6: Configuring Caddy Reverse Proxy + SSL"

$CADDY_FILE = "$APP_DIR\Caddyfile"

@"
$DOMAIN {
    reverse_proxy localhost:8000

    # Enable HTTPS with automatic Let's Encrypt certificates
    tls $SSL_EMAIL
}
"@ | Set-Content $CADDY_FILE

Write-Success "Caddyfile created"

# Open firewall ports
Write-Info "Opening firewall ports 80 and 443..."
try {
    New-NetFirewallRule -DisplayName "HTTP" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
    New-NetFirewallRule -DisplayName "HTTPS" -Direction Inbound -LocalPort 443 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue | Out-Null
    Write-Success "Firewall ports opened"
} catch {
    Write-Warn "Could not configure firewall. Manually open ports 80 and 443."
}

# ══════════════════════════════════════════════════════════════════════
#  STEP 6: Create Startup Scripts & Scheduled Task
# ══════════════════════════════════════════════════════════════════════
Write-Header "Step 6/6: Creating Auto-Start"

# Create a startup batch script
$START_SCRIPT = "$APP_DIR\start.bat"
@"
@echo off
cd /d "$APP_DIR"
start /B "$VENV_UVICORN" app.main:app --host 127.0.0.1 --port 8000
start /B caddy run --config "$CADDY_FILE"
"@ | Set-Content $START_SCRIPT

# Create Task Scheduler entry
$taskName = "YouTubeShortsGenerator"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$START_SCRIPT`"" -WorkingDirectory $APP_DIR
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "YouTube Shorts Generator" | Out-Null

Write-Success "Scheduled task created: $taskName"

# ── Start now ──
Write-Info "Starting application..."
Start-Process -WindowStyle Hidden -FilePath $VENV_UVICORN -ArgumentList "app.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $APP_DIR
Start-Sleep 2

$caddyCmd = Get-Command caddy -ErrorAction SilentlyContinue
if ($caddyCmd) {
    Start-Process -WindowStyle Hidden -FilePath "caddy" -ArgumentList "run --config `"$CADDY_FILE`"" -WorkingDirectory $APP_DIR
    Start-Sleep 2
}

# ══════════════════════════════════════════════════════════════════════
#  DONE!
# ══════════════════════════════════════════════════════════════════════
Write-Header "Installation Complete! 🎉"

Write-Host "Your YouTube Shorts Generator is now running!" -ForegroundColor Green
Write-Host ""
Write-Host "  Web UI:           https://$DOMAIN" -ForegroundColor Cyan
Write-Host "  Admin Dashboard:  https://$DOMAIN/admin" -ForegroundColor Cyan
Write-Host "  YouTube Callback: https://$DOMAIN/api/youtube/callback" -ForegroundColor Cyan
Write-Host ""
Write-Host "  App Directory:    $APP_DIR" -ForegroundColor White
Write-Host "  Restart:          schtasks /Run /TN $taskName" -ForegroundColor Yellow
Write-Host "  Stop:             taskkill /F /IM uvicorn.exe" -ForegroundColor Yellow
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Go to https://$DOMAIN/admin"
Write-Host "  2. Add your API keys (Gemini, Pexels, Telegram Bot Token)"
Write-Host "  3. Create your channels"
Write-Host "  4. For YouTube upload: add OAuth creds and set redirect URI to:"
Write-Host "     https://$DOMAIN/api/youtube/callback"
Write-Host "  5. Add team members and set up cron jobs"
Write-Host ""
