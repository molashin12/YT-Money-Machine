"""
Card Builder (Pillow) — composites a fact card from the channel's template
using pure Python image manipulation. No AI calls needed.

Layout (matching reference design):
- Top area: Channel logo/name (baked into template)
- Main area: Fact body text (large, white, left-aligned — fills most of card)
- Bottom area: Related image (fill-to-fit, ~30% of card)
- Bottom-left: Source attribution (small text)
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

# Font sizes — body is the star, fills most of the card
BODY_SIZE = 52
SOURCE_SIZE = 18


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load Inter font at given size."""
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
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


def _measure_text_height(draw: ImageDraw.Draw, lines: list[str], font: ImageFont.FreeTypeFont, spacing: int = 8) -> int:
    """Calculate total height of wrapped text lines."""
    total = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        total += bbox[3] - bbox[1] + spacing
    return total


def build_card_pillow(
    channel: ChannelConfig,
    title: str,
    body: str,
    related_image: Optional[bytes] = None,
    image_source: str = "",
) -> Optional[bytes]:
    """
    Build a card image using Pillow (no AI).

    Layout (matching reference):
    - Body text: large, white, left-aligned — fills the majority of the card
    - Related image: below body text, fills remaining space
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
        source_font = _load_font(SOURCE_SIZE)

        # ── Layout zones ──
        padding = int(tw * 0.05)  # 5% padding on each side
        text_max_w = tw - (padding * 2)

        # Text starts below the logo/header area (top ~10% of template)
        text_start_y = int(th * 0.10)

        # Source zone at the very bottom
        source_y = int(th * 0.93)

        # Image zone ends just above source text
        img_zone_bottom = int(th * 0.90)

        # ── Auto-size body font to fill available space ──
        # Start with large font and shrink if text is too long
        # Target: text fills from text_start_y to about 70% of card
        max_text_zone_height = int(th * 0.70)  # Up to 70% of card for text

        body_text = body
        font_size = BODY_SIZE
        body_font = _load_font(font_size)
        body_lines = _wrap_text(draw, body_text, body_font, text_max_w)
        text_height = _measure_text_height(draw, body_lines, body_font, spacing=12)

        # Shrink font if body text overflows allocated zone
        while text_height > max_text_zone_height and font_size > 28:
            font_size -= 2
            body_font = _load_font(font_size)
            body_lines = _wrap_text(draw, body_text, body_font, text_max_w)
            text_height = _measure_text_height(draw, body_lines, body_font, spacing=12)

        logger.info(f"Body font size: {font_size}px, {len(body_lines)} lines")

        # ── Draw body text (left-aligned, WHITE) ──
        y = text_start_y
        line_spacing = 12
        for line in body_lines:
            bbox = draw.textbbox((0, 0), line, font=body_font)
            x = padding  # Left-aligned
            # Shadow for readability
            draw.text((x + 2, y + 2), line, font=body_font, fill="#00000099")
            # White text
            draw.text((x, y), line, font=body_font, fill="#ffffff")
            y += bbox[3] - bbox[1] + line_spacing

        # ── Place related image (below body text) ──
        img_zone_top = y + 20  # 20px gap after text
        img_zone_w = tw - (padding * 2)
        img_zone_h = img_zone_bottom - img_zone_top

        if related_image and img_zone_h > 80:
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

                # Center crop to fit zone
                cx = (new_w - img_zone_w) // 2
                cy = (new_h - img_zone_h) // 2
                img = img.crop((cx, cy, cx + img_zone_w, cy + img_zone_h))

                # Round corners
                img = _round_corners(img, radius=16)

                template.paste(img, (padding, img_zone_top), img)
                logger.info(f"Placed image at y={img_zone_top}, size={img_zone_w}x{img_zone_h}")
            except Exception as e:
                logger.warning(f"Failed to place related image: {e}")
        else:
            if not related_image:
                logger.warning("No related image provided for card")
            elif img_zone_h <= 80:
                logger.warning(f"Not enough space for image (only {img_zone_h}px available)")

        # ── Draw source attribution ──
        source_text = image_source if image_source else "source: web"
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
