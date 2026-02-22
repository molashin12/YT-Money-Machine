# YouTube Shorts AI Automation

AI-powered automation system that converts text, images, or social media links into 5-second branded YouTube Shorts videos. Accessible via **Telegram bot** and a **Web UI**.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Install FFmpeg

FFmpeg must be installed and available in your PATH.

- **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html) or `winget install ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg`

### 3. Configure

1. Copy `.env.example` to `.env` and fill in your API keys:
   ```
   GEMINI_API_KEY=your_key
   PEXELS_API_KEY=your_key
   TELEGRAM_BOT_TOKEN=your_token
   ```

2. Edit `channels.json` to add your channels.

3. For each channel, create a folder at `assets/channels/<slug>/` and add:
   - **`template.png`** — Your pre-designed card template (with logo, name, verified badge baked in, but empty text/image areas)
   - **`logo.png`** — Channel logo (optional, used if needed)

4. Add royalty-free music clips (5s, MP3) to `assets/music/`.

### 4. Run

```bash
uvicorn app.main:app --reload --port 8000
```

- **Web UI**: http://localhost:8000
- **Telegram Bot**: Send a message to your bot

## Input Types

| Input | Example |
|-------|---------|
| Text | A fun fact or piece of information |
| URL | TikTok, Instagram Reel, Facebook Reel, YouTube Short |
| Image | Photo with or without caption |

## Project Structure

```
app/
├── main.py              # FastAPI + Telegram entry point
├── config.py            # Settings and channel configs
├── pipeline.py          # Orchestrator
├── services/            # Core processing services
│   ├── content_extractor.py
│   ├── fact_extractor.py
│   ├── image_search.py
│   ├── card_builder.py
│   ├── stock_video.py
│   ├── music_selector.py
│   └── video_assembler.py
├── bot/                 # Telegram bot
│   ├── handlers.py
│   └── keyboards.py
└── web/                 # Web interface
    ├── routes.py
    └── templates/
        └── index.html
```
