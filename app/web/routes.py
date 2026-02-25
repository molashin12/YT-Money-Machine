"""
Web API Routes — FastAPI endpoints for the web interface and admin panel.
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.config import load_channels
from app.pipeline import generate_video
from app import settings_store
from app.services.api_key_manager import get_key_manager, reload_key_manager

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


# ── Main Pages ─────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the upload page."""
    channels = load_channels()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "channels": channels},
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Serve the admin dashboard."""
    return templates.TemplateResponse("admin.html", {"request": request})


# ── Video Generation ───────────────────────────────────────────────────


@router.post("/generate")
async def generate(
    channel: str = Form(...),
    text: str = Form(""),
    image: UploadFile = File(None),
):
    """Generate a video from the provided input."""
    image_bytes = None
    if image and image.filename:
        image_bytes = await image.read()

    if not text and not image_bytes:
        return JSONResponse(
            status_code=400,
            content={"error": "Please provide text, a URL, or an image."},
        )

    result = await generate_video(
        channel_slug=channel,
        text=text,
        image_bytes=image_bytes,
    )

    if result and Path(result.video_path).exists():
        # Return JSON with video download URL and YT metadata
        video_filename = Path(result.video_path).name
        return JSONResponse(content={
            "success": True,
            "video_url": f"/output/{video_filename}",
            "yt_title": result.yt_title,
            "yt_description": result.yt_description,
            "yt_hashtags": result.yt_hashtags,
            "fact_title": result.fact_title,
            "fact_body": result.fact_body,
        })
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "Video generation failed. Check server logs."},
        )


# ── Channel API ────────────────────────────────────────────────────────


@router.get("/api/channels")
async def api_list_channels():
    """List all channels."""
    channels = settings_store.list_channels()
    # Add template status
    for ch in channels:
        slug = ch.get("slug", "")
        ch_dir = settings_store.CHANNELS_DIR / slug
        ch["has_template"] = (ch_dir / "template.png").exists()
        ch["has_svg_template"] = (ch_dir / "template.svg").exists()
        ch["has_logo"] = (ch_dir / "logo.png").exists()
    return channels


@router.post("/api/channels")
async def api_create_channel(request: Request):
    """Create a new channel."""
    data = await request.json()
    if not data.get("name"):
        return JSONResponse(status_code=400, content={"error": "Channel name is required"})
    channel = settings_store.add_channel(data)
    return channel


@router.put("/api/channels/{slug}")
async def api_update_channel(slug: str, request: Request):
    """Update a channel's settings."""
    data = await request.json()
    updated = settings_store.update_channel(slug, data)
    if updated:
        return updated
    return JSONResponse(status_code=404, content={"error": "Channel not found"})


@router.delete("/api/channels/{slug}")
async def api_delete_channel(slug: str):
    """Delete a channel."""
    if settings_store.delete_channel(slug):
        return {"ok": True}
    return JSONResponse(status_code=404, content={"error": "Channel not found"})


@router.post("/api/channels/{slug}/template")
async def api_upload_template(slug: str, file: UploadFile = File(...)):
    """Upload a card template for a channel."""
    if not settings_store.get_channel(slug):
        return JSONResponse(status_code=404, content={"error": "Channel not found"})
    image_bytes = await file.read()
    path = settings_store.save_channel_template(slug, image_bytes)
    return {"ok": True, "path": path}


@router.post("/api/channels/{slug}/svg-template")
async def api_upload_svg_template(slug: str, file: UploadFile = File(...)):
    """Upload an SVG card template for a channel."""
    if not settings_store.get_channel(slug):
        return JSONResponse(status_code=404, content={"error": "Channel not found"})
    svg_bytes = await file.read()
    path = settings_store.save_channel_svg_template(slug, svg_bytes)
    return {"ok": True, "path": path}


@router.post("/api/channels/{slug}/logo")
async def api_upload_logo(slug: str, file: UploadFile = File(...)):
    """Upload a logo for a channel."""
    if not settings_store.get_channel(slug):
        return JSONResponse(status_code=404, content={"error": "Channel not found"})
    image_bytes = await file.read()
    path = settings_store.save_channel_logo(slug, image_bytes)
    return {"ok": True, "path": path}


# ── API Key Management ─────────────────────────────────────────────────


@router.get("/api/keys")
async def api_get_all_keys():
    """Get all API key configurations (keys are masked)."""
    data = settings_store.get_settings().get("api_keys", {})
    result = {}
    for service in ["gemini", "pexels", "google_cse"]:
        svc = data.get(service, {"keys": [], "cycling": False})
        result[service] = {
            "keys": [_mask_key(k) for k in svc.get("keys", [])],
            "key_count": len(svc.get("keys", [])),
            "cycling": svc.get("cycling", False),
        }
    result["google_cse_cx"] = data.get("google_cse_cx", "") or ""
    # Bot token
    bot_token = data.get("telegram_bot_token", "")
    result["telegram_bot_token"] = _mask_key(bot_token) if bot_token else ""
    return result


@router.post("/api/keys/{service}")
async def api_add_key(service: str, request: Request):
    """Add an API key for a service."""
    data = await request.json()
    key = data.get("key", "").strip()
    if not key:
        return JSONResponse(status_code=400, content={"error": "Key is required"})

    settings_store.add_api_key(service, key)
    reload_key_manager()
    return {"ok": True}


@router.delete("/api/keys/{service}/{index}")
async def api_delete_key(service: str, index: int):
    """Remove an API key by index."""
    settings_store.remove_api_key(service, index)
    reload_key_manager()
    return {"ok": True}


@router.put("/api/keys/{service}/cycling")
async def api_set_cycling(service: str, request: Request):
    """Toggle key cycling for a service."""
    data = await request.json()
    enabled = data.get("enabled", False)
    settings_store.set_cycling(service, enabled)
    reload_key_manager()
    return {"ok": True, "cycling": enabled}


# ── Music ──────────────────────────────────────────────────────────────


@router.get("/api/music")
async def api_list_music():
    """List available music files."""
    return settings_store.list_music_files()


# ── Settings Overview ──────────────────────────────────────────────────


@router.get("/api/settings")
async def api_get_settings():
    """Get full settings (for admin UI)."""
    data = settings_store.get_settings()
    # Mask API keys
    for service in ["gemini", "pexels", "google_cse"]:
        if service in data.get("api_keys", {}):
            data["api_keys"][service]["keys"] = [
                _mask_key(k) for k in data["api_keys"][service].get("keys", [])
            ]
    return data


@router.get("/api/settings/export")
async def api_export_settings():
    """Export the full settings.json file."""
    if not settings_store.SETTINGS_FILE.exists():
        return JSONResponse(status_code=404, content={"error": "Settings file not found"})
    return FileResponse(
        settings_store.SETTINGS_FILE,
        media_type="application/json",
        filename="settings.json"
    )


@router.post("/api/settings/import")
async def api_import_settings(file: UploadFile = File(...)):
    """Import a settings.json file."""
    import json
    try:
        content = await file.read()
        data = json.loads(content)
        settings_store.save_settings(data)
        
        # Reload key manager and scheduler to pick up changes
        reload_key_manager()
        from app.scheduler import reload_jobs
        reload_jobs()
        
        return {"ok": True, "message": "Settings imported successfully."}
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON file"})
    except Exception as e:
        logger.error(f"Failed to import settings: {e}")
        return JSONResponse(status_code=500, content={"error": f"Failed to import: {str(e)}"})


# ── Cron Jobs ──────────────────────────────────────────────────────────


@router.get("/api/cron")
async def api_list_cron_jobs():
    """List all cron jobs."""
    return settings_store.list_cron_jobs()


@router.post("/api/cron")
async def api_create_cron_job(request: Request):
    """Create a new cron job."""
    data = await request.json()
    if not data.get("channel_slug"):
        return JSONResponse(status_code=400, content={"error": "Channel is required"})
    job = settings_store.add_cron_job(data)
    # Reload scheduler
    from app.scheduler import reload_jobs
    reload_jobs()
    return job


@router.put("/api/cron/{job_id}")
async def api_update_cron_job(job_id: str, request: Request):
    """Update a cron job."""
    data = await request.json()
    updated = settings_store.update_cron_job(job_id, data)
    if updated:
        from app.scheduler import reload_jobs
        reload_jobs()
        return updated
    return JSONResponse(status_code=404, content={"error": "Job not found"})


@router.delete("/api/cron/{job_id}")
async def api_delete_cron_job(job_id: str):
    """Delete a cron job."""
    if settings_store.delete_cron_job(job_id):
        from app.scheduler import reload_jobs
        reload_jobs()
        return {"ok": True}
    return JSONResponse(status_code=404, content={"error": "Job not found"})


@router.post("/api/cron/{job_id}/trigger")
async def api_trigger_cron_job(job_id: str):
    """Manually trigger a cron job for testing."""
    job = settings_store.get_cron_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job not found"})

    from app.scheduler import _run_cron_job
    import asyncio
    asyncio.create_task(_run_cron_job(job))
    return {"ok": True, "message": "Job triggered — check Telegram for ideas"}


# ── Team Members ───────────────────────────────────────────────────────


@router.get("/api/team")
async def api_list_team():
    """List team members."""
    return settings_store.list_team_members()


@router.post("/api/team")
async def api_add_team_member(request: Request):
    """Add a team member."""
    data = await request.json()
    name = data.get("name", "").strip()
    chat_id = data.get("chat_id")
    if not name or not chat_id:
        return JSONResponse(status_code=400, content={"error": "Name and chat ID are required"})
    member = settings_store.add_team_member(name, int(chat_id))
    return member


@router.delete("/api/team/{chat_id}")
async def api_delete_team_member(chat_id: int):
    """Remove a team member."""
    if settings_store.delete_team_member(chat_id):
        return {"ok": True}
    return JSONResponse(status_code=404, content={"error": "Member not found"})


# ── Logs ───────────────────────────────────────────────────────────────


@router.get("/api/logs")
async def api_get_logs(after: int = 0, limit: int = 200):
    """Get recent log entries for the admin dashboard."""
    from app.log_handler import get_log_handler
    handler = get_log_handler()
    if after > 0:
        logs, counter = handler.get_logs(after=after, limit=limit)
    else:
        logs, counter = handler.get_all(limit=limit)
    return {"logs": logs, "counter": counter}


# ── YouTube OAuth ──────────────────────────────────────────────────────


@router.post("/api/youtube/config")
async def api_save_youtube_config(request: Request):
    """Save YouTube OAuth client credentials."""
    data = await request.json()
    client_id = data.get("client_id", "").strip()
    client_secret = data.get("client_secret", "").strip()
    if not client_id or not client_secret:
        return JSONResponse(status_code=400, content={"error": "Both client_id and client_secret required"})

    settings = settings_store.get_settings()
    settings["api_keys"]["youtube_oauth"] = {
        "client_id": client_id,
        "client_secret": client_secret,
    }
    settings_store._write_settings(settings)
    return {"ok": True}


@router.get("/api/youtube/config")
async def api_get_youtube_config():
    """Get YouTube OAuth config (masked)."""
    settings = settings_store.get_settings()
    yt = settings.get("api_keys", {}).get("youtube_oauth", {})
    return {
        "client_id": _mask_key(yt.get("client_id", "")) if yt.get("client_id") else "",
        "client_secret": _mask_key(yt.get("client_secret", "")) if yt.get("client_secret") else "",
        "configured": bool(yt.get("client_id") and yt.get("client_secret")),
    }


@router.get("/api/youtube/auth/{channel_slug}")
async def api_youtube_auth(channel_slug: str, request: Request):
    """Redirect to Google OAuth consent for a channel."""
    from app.services.youtube_uploader import get_auth_url

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/youtube/callback"

    auth_url = get_auth_url(channel_slug, redirect_uri)
    if not auth_url:
        return JSONResponse(
            status_code=400,
            content={"error": "YouTube OAuth not configured. Add client ID and secret first."},
        )
    from starlette.responses import RedirectResponse
    return RedirectResponse(url=auth_url)


@router.get("/api/youtube/callback")
async def api_youtube_callback(request: Request):
    """Handle OAuth callback from Google."""
    from app.services.youtube_uploader import handle_callback

    code = request.query_params.get("code")
    channel_slug = request.query_params.get("state")

    if not code or not channel_slug:
        return HTMLResponse("<h2>❌ OAuth failed: missing code or state</h2>")

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/youtube/callback"

    success = handle_callback(code, channel_slug, redirect_uri)
    if success:
        return HTMLResponse(
            f"<h2>✅ YouTube connected for channel: {channel_slug}</h2>"
            "<p>You can close this window and go back to the admin dashboard.</p>"
        )
    else:
        return HTMLResponse("<h2>❌ OAuth failed. Check server logs.</h2>")


@router.get("/api/youtube/status/{channel_slug}")
async def api_youtube_status(channel_slug: str):
    """Check if a channel is connected to YouTube."""
    from app.services.youtube_uploader import is_channel_connected
    return {"connected": is_channel_connected(channel_slug)}


# ── Helpers ────────────────────────────────────────────────────────────


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 and last 4 chars."""
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:4]}{'•' * (len(key) - 8)}{key[-4:]}"
