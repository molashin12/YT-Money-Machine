"""
Video Assembler — uses FFmpeg to compose the final 5-second video.

Layers (bottom to top):
1. Background stock video (scaled/cropped to 1080x1920)
2. Card overlay PNG (centered, with margins showing background)
3. Background music

Output: MP4 (H.264 + AAC), exactly 5 seconds.
"""

import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from app.config import VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS, OUTPUT_DIR

logger = logging.getLogger(__name__)


def assemble_video(
    card_image_bytes: bytes,
    background_video_path: str,
    music_path: Optional[str] = None,
    duration: int = 5,
) -> Optional[str]:
    """
    Assemble the final video using FFmpeg.

    Args:
        card_image_bytes: PNG bytes of the card overlay (with transparency)
        background_video_path: Path to the background video file
        music_path: Path to the music file (optional)
        duration: Video duration in seconds (default 5)

    Returns:
        Path to the generated MP4 file, or None on failure.
    """
    output_filename = f"video_{uuid.uuid4().hex[:12]}.mp4"
    output_path = OUTPUT_DIR / output_filename

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Write card image to temp file
        card_path = Path(tmp_dir) / "card.png"
        card_path.write_bytes(card_image_bytes)

        # Build FFmpeg command
        cmd = _build_ffmpeg_command(
            background_video=background_video_path,
            card_overlay=str(card_path),
            music=music_path,
            output=str(output_path),
            duration=duration,
        )

        logger.info(f"Running FFmpeg: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg failed:\n{result.stderr}")
                return None

            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info(f"Video created: {output_path}")
                return str(output_path)
            else:
                logger.error("FFmpeg produced no output file")
                return None

        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out")
            return None
        except FileNotFoundError:
            logger.error(
                "FFmpeg not found! Please install FFmpeg and ensure it's in your PATH."
            )
            return None


def _build_ffmpeg_command(
    background_video: str,
    card_overlay: str,
    music: Optional[str],
    output: str,
    duration: int = 5,
) -> list[str]:
    """Build the FFmpeg command with all filters."""

    # Base inputs
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output
        "-stream_loop", "-1",  # Loop background video if shorter than 5s
        "-i", background_video,  # Input 0: background video
        "-i", card_overlay,  # Input 1: card overlay PNG
    ]

    # Add music input if available
    if music:
        cmd.extend(["-i", music])  # Input 2: music

    # Video filter: scale+crop background, then overlay card
    vf = (
        # Scale background to fill 1080x1920, cropping excess
        f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        f"fps={VIDEO_FPS},"
        f"setsar=1[bg];"
        # Scale card overlay to exact video size (it's already sized correctly)
        f"[1:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}[card];"
        # Overlay card on background
        f"[bg][card]overlay=0:0:format=auto[out]"
    )

    cmd.extend(["-filter_complex", vf])
    cmd.extend(["-map", "[out]"])

    # Audio handling
    if music:
        # Use music audio, trim to duration
        cmd.extend(["-map", "2:a"])
        cmd.extend(["-af", f"afade=t=out:st={duration - 0.5}:d=0.5"])
    else:
        # No audio — silent video
        cmd.extend(["-an"])

    # Output settings
    cmd.extend([
        "-t", str(duration),  # Duration in seconds
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",  # Web-friendly
    ])

    if music:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

    cmd.append(output)
    return cmd
