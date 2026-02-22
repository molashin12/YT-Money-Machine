"""
YouTube Uploader — handles OAuth2 authentication and video upload
to YouTube as private (draft) videos via the YouTube Data API v3.

Setup:
1. Create a Google Cloud project with YouTube Data API v3 enabled
2. Create OAuth2 Web Application credentials
3. Set redirect URI to: http://localhost:8000/api/youtube/callback
4. Save client_id + client_secret in admin dashboard
"""

import json
import logging
from pathlib import Path
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app import settings_store

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"


def _get_oauth_config() -> Optional[dict]:
    """Get YouTube OAuth client credentials from settings."""
    data = settings_store.get_settings()
    yt = data.get("api_keys", {}).get("youtube_oauth", {})
    if yt.get("client_id") and yt.get("client_secret"):
        return yt
    return None


def _get_channel_tokens(channel_slug: str) -> Optional[dict]:
    """Get stored YouTube OAuth tokens for a channel."""
    ch = settings_store.get_channel(channel_slug)
    if ch and ch.get("youtube_tokens", {}).get("refresh_token"):
        return ch["youtube_tokens"]
    return None


def is_channel_connected(channel_slug: str) -> bool:
    """Check if a channel has valid YouTube OAuth tokens."""
    return _get_channel_tokens(channel_slug) is not None


def get_auth_url(channel_slug: str, redirect_uri: str) -> Optional[str]:
    """Generate Google OAuth2 consent URL for a channel."""
    config = _get_oauth_config()
    if not config:
        logger.error("YouTube OAuth client credentials not configured")
        return None

    client_config = {
        "web": {
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = redirect_uri

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=channel_slug,  # Pass channel_slug as state for callback
    )
    return auth_url


def handle_callback(auth_code: str, channel_slug: str, redirect_uri: str) -> bool:
    """Exchange authorization code for tokens and store them."""
    config = _get_oauth_config()
    if not config:
        return False

    try:
        client_config = {
            "web": {
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        }

        flow = Flow.from_client_config(client_config, scopes=SCOPES)
        flow.redirect_uri = redirect_uri
        flow.fetch_token(code=auth_code)

        credentials = flow.credentials

        # Store tokens for the channel
        tokens = {
            "refresh_token": credentials.refresh_token,
            "token": credentials.token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        }

        # Update channel with YouTube tokens
        settings_store.update_channel(channel_slug, {"youtube_tokens": tokens})
        logger.info(f"YouTube OAuth tokens stored for channel: {channel_slug}")
        return True

    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        return False


def _get_youtube_service(channel_slug: str):
    """Build a YouTube API service for a connected channel."""
    tokens = _get_channel_tokens(channel_slug)
    if not tokens:
        raise ValueError(f"Channel '{channel_slug}' is not connected to YouTube")

    credentials = Credentials(
        token=tokens.get("token"),
        refresh_token=tokens["refresh_token"],
        token_uri=tokens.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=tokens.get("client_id"),
        client_secret=tokens.get("client_secret"),
    )

    return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=credentials)


async def upload_to_youtube(
    channel_slug: str,
    video_path: str,
    title: str = "",
    description: str = "",
    tags: list[str] = None,
) -> Optional[dict]:
    """
    Upload a video to YouTube as a PRIVATE (draft) video.

    Returns dict with video_id and url on success, None on failure.
    """
    if not is_channel_connected(channel_slug):
        logger.error(f"Channel '{channel_slug}' is not connected to YouTube")
        return None

    try:
        youtube = _get_youtube_service(channel_slug)

        # Clean tags — remove # prefix
        clean_tags = [t.lstrip("#") for t in (tags or [])]

        body = {
            "snippet": {
                "title": title or "YouTube Short",
                "description": description or "",
                "tags": clean_tags,
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": "private",  # Draft — not publicly visible
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
            },
        }

        # Resumable upload
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024,  # 1MB chunks
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        logger.info(f"Uploading video to YouTube: {title}")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        if video_id:
            video_url = f"https://youtube.com/shorts/{video_id}"
            logger.info(f"Upload complete! Video ID: {video_id}, URL: {video_url}")
            return {
                "video_id": video_id,
                "url": video_url,
                "status": "private",
            }
        else:
            logger.error("Upload returned no video ID")
            return None

    except Exception as e:
        logger.error(f"YouTube upload failed: {e}")
        return None
