"""
Card Builder (Pillow) — composites a fact card from the channel's template
using pure Python image manipulation. No AI calls needed.

Layout:
- Top area: Title (bold, large) + body text (regular)
- Bottom area: Related image (fill-to-fit)
- Bottom-left: Source attribution (small text)
- Uses channel colors, Inter font
"""

import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.config import (
    ChannelConfig,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    CARD_MARGIN,
    FONTS_DIR,
)

logger = logging.getLogger(__name__)

# Font paths
FONT_PATH = FONTS_DIR / "Inter.ttf"

# Fallback font sizes
TITLE_SIZE = 42
BODY_SIZE = 28
SOURCE_SIZE = 18


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load Inter font at given size."""
    try:
        # Inter variable font — use higher weight for bold
        font = ImageFont.truetype(str(FONT_PATH), size)
        return font
    except Exception:
        logger.warning("Inter font not found, using default")
        return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text to fit within max_width."""
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    return lines or [""]


def build_card_pillow(
    channel: ChannelConfig,
    title: str,
    body: str,
    related_image: Optional[bytes] = None,
    image_source: str = "",
) -> Optional[bytes]:
    """
    Build a card image using Pillow (no AI).

    Layout on the template:
    - Title text: top portion, left-aligned, accent color
    - Body text: below title, left-aligned, WHITE
    - Related image: lower portion, fill-to-fit
    - Source: bottom-left corner, small text

    Returns PNG bytes on a 1080×1920 transparent canvas.
    """
    logger.info(f"Building card with Pillow for channel: {channel.name}")

    # Load template
    if not channel.template_path or not Path(channel.template_path).exists():
        logger.error(
            f"No template found for channel '{channel.slug}'. "
            f"Please add a template to: assets/channels/{channel.slug}/template.png"
        )
        return None

    try:
        template = Image.open(channel.template_path).convert("RGBA")
        tw, th = template.size
        draw = ImageDraw.Draw(template)

        # Load fonts
        title_font = _load_font(TITLE_SIZE, bold=True)
        body_font = _load_font(BODY_SIZE)
        source_font = _load_font(SOURCE_SIZE)

        # Colors
        accent_color = channel.accent_color or "#e94560"

        # ── Layout zones (relative to template size) ──
        padding = int(tw * 0.06)  # 6% padding
        text_max_w = tw - (padding * 2)

        # Text zone: top 18% to 60% of template
        text_zone_top = int(th * 0.18)  # Below logo area

        # ── Draw title (left-aligned, accent color) ──
        title_lines = _wrap_text(draw, title.upper(), title_font, text_max_w)
        y = text_zone_top
        for line in title_lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            x = padding  # Left-aligned
            # Shadow
            draw.text((x + 2, y + 2), line, font=title_font, fill="#00000088")
            draw.text((x, y), line, font=title_font, fill=accent_color)
            y += bbox[3] - bbox[1] + 8

        # ── Draw body (left-aligned, always WHITE) ──
        y += 16  # Gap between title and body
        body_lines = _wrap_text(draw, body, body_font, text_max_w)

        # Calculate where the image zone should start (after body text)
        body_end_y = y
        for line in body_lines:
            bbox = draw.textbbox((0, 0), line, font=body_font)
            body_end_y += bbox[3] - bbox[1] + 6
        body_end_y += 20  # Buffer space

        for line in body_lines:
            bbox = draw.textbbox((0, 0), line, font=body_font)
            x = padding  # Left-aligned
            # Shadow
            draw.text((x + 1, y + 1), line, font=body_font, fill="#00000088")
            draw.text((x, y), line, font=body_font, fill="#ffffff")  # Always white
            y += bbox[3] - bbox[1] + 6

        # ── Place related image (below body text) ──
        # Image zone starts after body text, uses remaining space
        img_zone_top = max(body_end_y, int(th * 0.48))
        img_zone_bottom = int(th * 0.88)
        img_zone_w = tw - (padding * 2)
        img_zone_h = img_zone_bottom - img_zone_top

        if related_image and img_zone_h > 50:
            try:
                img = Image.open(io.BytesIO(related_image)).convert("RGBA")

                # Scale to fill image zone
                img_ratio = img.width / img.height
                zone_ratio = img_zone_w / img_zone_h

                if img_ratio > zone_ratio:
                    new_h = img_zone_h
                    new_w = int(new_h * img_ratio)
                else:
                    new_w = img_zone_w
                    new_h = int(new_w / img_ratio)

                img = img.resize((new_w, new_h), Image.LANCZOS)

                # Center crop
                cx = (new_w - img_zone_w) // 2
                cy = (new_h - img_zone_h) // 2
                img = img.crop((cx, cy, cx + img_zone_w, cy + img_zone_h))

                # Round corners
                img = _round_corners(img, radius=16)

                template.paste(img, (padding, img_zone_top), img)
            except Exception as e:
                logger.warning(f"Failed to place related image: {e}")

        # ── Draw source attribution ──
        source_text = image_source if image_source else "source: web"
        source_y = int(th * 0.91)
        draw.text(
            (padding, source_y), source_text,
            font=source_font, fill="#ffffff88",
        )

        # ── Compose on 1080×1920 canvas ──
        return _compose_on_canvas(template)

    except Exception as e:
        logger.exception(f"Pillow card building failed: {e}")
        return None


def _round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Add rounded corners to an image."""
    from PIL import ImageDraw as ID
    mask = Image.new("L", img.size, 0)
    d = ID.Draw(mask)
    d.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def _compose_on_canvas(card: Image.Image) -> bytes:
    """Place the card onto a 1080×1920 transparent canvas, centered with margins."""
    canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))

    target_w = VIDEO_WIDTH - (CARD_MARGIN * 2)
    target_h = VIDEO_HEIGHT - (CARD_MARGIN * 2)

    card_ratio = card.width / card.height
    target_ratio = target_w / target_h

    if card_ratio > target_ratio:
        new_w = target_w
        new_h = int(new_w / card_ratio)
    else:
        new_h = target_h
        new_w = int(new_h * card_ratio)

    card = card.resize((new_w, new_h), Image.LANCZOS)

    x = (VIDEO_WIDTH - new_w) // 2
    y = (VIDEO_HEIGHT - new_h) // 2

    canvas.paste(card, (x, y), card)

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()
