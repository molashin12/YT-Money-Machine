# 🎬 YT Money Machine

AI-powered automation system that converts text, images, or social media links into branded YouTube Shorts videos. Manage everything via **Telegram Bot** + **Web Admin Dashboard**. Schedule automated content generation, approve ideas from your phone, and upload directly to YouTube as drafts.

> Made with ❤️ by **Dr Molashin**

---

## ✨ Features

- 🤖 **AI-Powered** — Gemini extracts facts, generates titles, descriptions & hashtags
- 🖼️ **Dual Card Builder** — Free Pillow mode or AI Gemini mode (configurable per channel)
- 📱 **Telegram Bot** — Send content, approve ideas, upload videos — all from your phone
- ⏰ **Cron Job Scheduler** — Auto-generate video ideas daily at scheduled times
- 👥 **Team Support** — Multiple users (Mohamed & Ahmed can divide work)
- 📤 **YouTube Upload** — Upload as private drafts via OAuth2, publish when ready
- 🔐 **SSL + Hosting** — One-command deployment with auto SSL certificates
- 🔑 **Admin Dashboard** — Manage API keys, channels, cron jobs, team members
- 🎵 **Music Integration** — Random, specific, or no background music per channel
- 🌐 **Multi-Input** — Text, images, URLs (TikTok, Instagram, Facebook, YouTube)

## 📋 Input Types

| Input | Example |
|-------|---------|
| Text | A fun fact or piece of information |
| URL | TikTok, Instagram Reel, Facebook Reel, YouTube Short |
| Image | Photo with or without caption |

---

## 🚀 Quick Start (Development)

### 1. Clone & Install

```bash
git clone https://github.com/molashin12/YT-Money-Machine.git
cd YT-Money-Machine
pip install -r requirements.txt
```

### 2. Install FFmpeg

- **Windows**: `winget install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg`

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:
```
BASE_URL=https://your-domain.com
BOT_MODE=polling
```

> **All API keys and channels are configured via the Admin Dashboard** at `/admin` — no need to edit config files.

### 4. Run

```bash
uvicorn app.main:app --reload --port 8000
```

- **Web UI**: http://localhost:8000
- **Admin**: http://localhost:8000/admin
- **Telegram Bot**: Send a message to your bot

---

## 🌍 Production Deployment (VPS)

Deploy to any Linux VPS or Windows server with one command:

```bash
# Linux (Ubuntu/Debian) — recommended
sudo python3 install.py

# Windows (Admin PowerShell)
python install.py
```

The installer will:
1. Install all dependencies (Python, FFmpeg, reverse proxy)
2. Ask for your domain name and SSL email
3. Set up **Nginx** (Linux) or **Caddy** (Windows) with **auto SSL**
4. Create a **systemd service** / **Task Scheduler** entry for auto-start
5. Configure everything — accessible at `https://your-domain.com`

> ⚠️ Point your domain's DNS A record to your server IP before running the installer.

---

## ⚙️ Admin Dashboard

Go to `/admin` to manage everything:

| Tab | What It Does |
|-----|--------------|
| 📺 **Channels** | Add/edit channels, set card mode (Pillow/AI), connect YouTube |
| 🔑 **API Keys** | Manage Gemini, Pexels, Google CSE, Telegram, YouTube OAuth |
| 👥 **Team Members** | Add team members (name + Telegram chat ID) |
| ⏰ **Cron Jobs** | Schedule auto idea generation per channel per team member |
| 🎵 **Music** | View available background music files |

---

## 📤 YouTube Upload Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create/select a project → Enable **YouTube Data API v3**
3. **Credentials** → Create **OAuth 2.0 Client ID** (Web application)
4. Add redirect URI: `https://your-domain.com/api/youtube/callback`
5. In Admin → 🔑 API Keys → 🎥 YouTube OAuth → paste Client ID + Secret
6. On 📺 Channels tab → click **🔗 YouTube** → authorize in browser

Videos uploaded as **private** (draft) — publish manually from YouTube Studio.

---

## 🔄 How It Works

```
Input (text/image/URL)
  → Content Extraction (Gemini Vision or yt-dlp)
  → Fact Extraction (Gemini, merged in 1 call)
  → Image Search (Google CSE or Pexels)
  → Card Building (Pillow or AI)
  → Video Assembly (FFmpeg)
  → Send to Telegram with 📤 Upload / ❌ Skip buttons
  → Upload to YouTube as private draft (if approved)
```

### Cron Job Flow
```
⏰ Scheduled time
  → Gemini generates N unique ideas (avoids past topics)
  → Each idea sent to Telegram with ✅ Approve / ❌ Skip
  → User approves ideas → taps 🚀 Generate Videos
  → Each video generated and sent with 📤 Upload / ❌ Skip
  → Approved videos uploaded to YouTube as drafts
```

---

## 📁 Project Structure

```
├── install.py               # One-command cross-platform installer
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
│
├── app/
│   ├── main.py              # FastAPI + Telegram entry point
│   ├── config.py            # Settings and channel configs
│   ├── pipeline.py          # Video generation orchestrator
│   ├── scheduler.py         # APScheduler cron job system
│   ├── settings_store.py    # JSON-based settings CRUD
│   │
│   ├── services/
│   │   ├── content_extractor.py   # Input parsing (URL/image/text)
│   │   ├── fact_extractor.py      # Gemini fact extraction
│   │   ├── idea_generator.py      # AI idea generation for cron
│   │   ├── image_search.py        # Google CSE / Pexels
│   │   ├── card_builder.py        # AI card builder (Gemini)
│   │   ├── card_builder_pillow.py # Free Pillow card builder
│   │   ├── video_assembler.py     # FFmpeg video composition
│   │   ├── video_history.py       # Past topics tracking
│   │   ├── youtube_uploader.py    # YouTube OAuth2 + upload
│   │   ├── music_selector.py      # Background music selection
│   │   ├── stock_video.py         # Stock video fetching
│   │   └── api_key_manager.py     # Key rotation
│   │
│   ├── bot/
│   │   ├── handlers.py      # Telegram conversation handlers
│   │   └── keyboards.py     # Inline keyboard builders
│   │
│   └── web/
│       ├── routes.py         # REST API endpoints
│       └── templates/
│           ├── index.html    # Video generator UI
│           └── admin.html    # Admin dashboard
│
├── scripts/
│   ├── install_linux.sh      # Linux deploy (Nginx/SSL/systemd)
│   └── install_windows.ps1   # Windows deploy (Caddy/SSL)
│
├── assets/
│   ├── fonts/Inter.ttf       # Font for Pillow cards
│   ├── music/                # Background music files
│   └── channels/<slug>/      # Per-channel assets
│       ├── template.png      # Card template
│       └── logo.png          # Channel logo
│
├── data/
│   ├── settings.json         # All settings & API keys
│   └── video_history.json    # Past video tracking
│
└── output/                   # Generated videos
```

---

## 🔧 Management Commands

| Action | Linux | Windows |
|--------|-------|---------|
| View logs | `journalctl -u youtube-shorts -f` | Task Scheduler logs |
| Restart | `systemctl restart youtube-shorts` | `schtasks /Run /TN YouTubeShortsGenerator` |
| Stop | `systemctl stop youtube-shorts` | `taskkill /F /IM uvicorn.exe` |
| SSL renewal | Automatic (Certbot timer) | Automatic (Caddy) |

---

## 📦 Requirements

- **Python** 3.10+
- **FFmpeg** (for video processing)
- **APIs**: Gemini (required), Pexels or Google CSE (images), Telegram Bot Token
- **Optional**: YouTube Data API v3 (for upload feature)

---

Made with ❤️ by **Dr Molashin**
