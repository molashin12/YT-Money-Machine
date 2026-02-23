"""
SVG Template Card Builder — renders cards from Figma-exported SVG templates.

Workflow:
1. Load the channel's SVG template (assets/channels/<slug>/template.svg)
2. Parse and manipulate the SVG using xml.etree.ElementTree
3. Inject dynamic content:
   - Text (wrapped into <tspan> elements) → id="input_text"
   - Image (base64-encoded) → id="main_image"
   - Source attribution → id="source"
4. Recalculate SVG height based on text length
5. Render to PNG via CairoSVG
6. Place on 1080×1920 transparent canvas
"""

import base64
import io
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from PIL import Image

from app.config import (
    ChannelConfig,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    CARD_MARGIN,
)

logger = logging.getLogger(__name__)

# ── Layout constants (tune these to match your template) ──────────────
TEXT_CHARS_PER_LINE = 38        # Characters per line before wrapping
LINE_HEIGHT = 28                # Pixels between lines (dy for <tspan>)
TEXT_FONT_SIZE = 22             # Font size in px for body text
TOP_PADDING = 80                # Space above the text (logo/header area)
TEXT_TO_IMAGE_GAP = 20          # Gap between text and image
IMAGE_TO_SOURCE_GAP = 12        # Gap between image and source text
SOURCE_HEIGHT = 20              # Source text line height
BOTTOM_PADDING = 30             # Space below the source text
DEFAULT_IMAGE_HEIGHT = 400      # Default image height if none specified
IMAGE_WIDTH_RATIO = 0.90        # Image width as fraction of SVG width

# SVG namespace
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def _wrap_text(text: str, max_chars: int = TEXT_CHARS_PER_LINE) -> list[str]:
    """Wrap text into lines of max_chars characters, breaking on word boundaries."""
    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test = f"{current_line} {word}".strip()
        if len(test) <= max_chars:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word

    if current_line:
        lines.append(current_line)

    return lines or [""]


def _find_element_by_id(root: ET.Element, elem_id: str) -> Optional[ET.Element]:
    """Find an element by its id attribute (searching all namespaces)."""
    # Try standard SVG namespace
    for elem in root.iter():
        if elem.get("id") == elem_id:
            return elem
    return None


def _get_svg_dimensions(root: ET.Element) -> tuple[float, float]:
    """Get the current width and height of the SVG."""
    width = float(root.get("width", "1080").replace("px", ""))
    height = float(root.get("height", "1920").replace("px", ""))
    return width, height


def _image_to_base64(image_bytes: bytes) -> str:
    """Convert image bytes to a base64 data URI."""
    # Detect MIME type
    if image_bytes[:8].startswith(b"\x89PNG"):
        mime = "image/png"
    elif image_bytes[:4].startswith(b"RIFF"):
        mime = "image/webp"
    elif image_bytes[:2] in (b"\xff\xd8",):
        mime = "image/jpeg"
    else:
        mime = "image/jpeg"  # Default to JPEG

    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _inject_text(root: ET.Element, text: str, svg_width: float) -> tuple[int, float]:
    """
    Inject wrapped text into the element with id="input_text".

    Returns (num_lines, text_block_height).
    """
    text_elem = _find_element_by_id(root, "input_text")
    if text_elem is None:
        logger.warning("SVG element id='input_text' not found — skipping text injection")
        return 1, LINE_HEIGHT

    # Get the original x position from the element
    x = text_elem.get("x", "54")

    # Get the original y position — this is the starting baseline
    base_y = float(text_elem.get("y", str(TOP_PADDING)))

    # Clear existing children and text
    text_elem.text = None
    text_elem.tail = None
    for child in list(text_elem):
        text_elem.remove(child)

    # Wrap text into lines
    lines = _wrap_text(text)

    # Create <tspan> for each line
    for i, line in enumerate(lines):
        tspan = ET.SubElement(text_elem, f"{{{SVG_NS}}}tspan")
        tspan.set("x", str(x))
        if i == 0:
            tspan.set("y", str(base_y))
        else:
            tspan.set("dy", str(LINE_HEIGHT))
        tspan.text = line

    text_height = len(lines) * LINE_HEIGHT
    logger.info(f"SVG text: {len(lines)} lines, {text_height}px height")

    return len(lines), text_height


def _inject_image(
    root: ET.Element,
    image_bytes: Optional[bytes],
    y_position: float,
    svg_width: float,
) -> float:
    """
    Inject base64 image into element with id="main_image".

    Returns the actual image height used.
    """
    image_elem = _find_element_by_id(root, "main_image")
    if image_elem is None:
        logger.warning("SVG element id='main_image' not found — skipping image injection")
        return DEFAULT_IMAGE_HEIGHT

    if not image_bytes:
        logger.warning("No image bytes provided for SVG card")
        return DEFAULT_IMAGE_HEIGHT

    # Convert image to base64
    data_uri = _image_to_base64(image_bytes)

    # Set the image source (try both href and xlink:href)
    image_elem.set("href", data_uri)
    image_elem.set(f"{{{XLINK_NS}}}href", data_uri)

    # Update y position based on text height
    image_elem.set("y", str(y_position))

    # Keep width fixed, calculate proportional height
    img_width = float(image_elem.get("width", str(int(svg_width * IMAGE_WIDTH_RATIO))))
    img_height = float(image_elem.get("height", str(DEFAULT_IMAGE_HEIGHT)))

    # Try to get actual aspect ratio from the image
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        aspect = pil_img.width / pil_img.height
        img_height = img_width / aspect
        image_elem.set("height", str(img_height))
    except Exception:
        pass  # Keep existing height

    logger.info(f"SVG image: y={y_position}, w={img_width}, h={img_height}")
    return img_height


def _inject_source(root: ET.Element, source_text: str, y_position: float) -> None:
    """Replace text of element with id='source' and reposition."""
    source_elem = _find_element_by_id(root, "source")
    if source_elem is None:
        logger.warning("SVG element id='source' not found — skipping source injection")
        return

    # Update position
    source_elem.set("y", str(y_position))

    # Clear and set text
    source_elem.text = source_text
    for child in list(source_elem):
        source_elem.remove(child)


def _update_svg_dimensions(root: ET.Element, new_height: float, svg_width: float) -> None:
    """Update the SVG height, viewBox, and background rect."""
    # Update SVG attributes
    root.set("height", str(int(new_height)))
    root.set("viewBox", f"0 0 {int(svg_width)} {int(new_height)}")

    # Update background rectangle if present (usually first <rect>)
    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        # Check if it's a background rect (full width or close to it)
        rect_width = float(rect.get("width", "0"))
        if rect_width >= svg_width * 0.9:
            rect.set("height", str(int(new_height)))
            logger.info(f"Updated background rect height to {int(new_height)}")
            break


async def build_card_svg(
    channel: ChannelConfig,
    title: str,
    body: str,
    related_image: Optional[bytes] = None,
    image_source: str = "",
) -> Optional[bytes]:
    """
    Build a card image by injecting content into an SVG template and rendering to PNG.

    Returns PNG bytes on a 1080×1920 transparent canvas, or None on failure.
    """
    logger.info(f"Building SVG card for channel: {channel.name}")

    # ── Load SVG template ──
    svg_path = channel.svg_template_path
    if not svg_path or not Path(svg_path).exists():
        logger.error(
            f"No SVG template found for channel '{channel.slug}'. "
            f"Please add: assets/channels/{channel.slug}/template.svg"
        )
        return None

    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
    except ET.ParseError as e:
        logger.error(f"Failed to parse SVG template: {e}")
        return None

    svg_width, original_height = _get_svg_dimensions(root)

    # ── Step 1: Inject text ──
    num_lines, text_height = _inject_text(root, body, svg_width)

    # ── Step 2: Calculate image Y position ──
    text_bottom = TOP_PADDING + text_height
    image_y = text_bottom + TEXT_TO_IMAGE_GAP

    # ── Step 3: Inject image ──
    image_height = _inject_image(root, related_image, image_y, svg_width)

    # ── Step 4: Inject source ──
    source_text = image_source if image_source else "source: web"
    source_y = image_y + image_height + IMAGE_TO_SOURCE_GAP
    _inject_source(root, source_text, source_y)

    # ── Step 5: Recalculate SVG height ──
    new_height = (
        TOP_PADDING
        + text_height
        + TEXT_TO_IMAGE_GAP
        + image_height
        + IMAGE_TO_SOURCE_GAP
        + SOURCE_HEIGHT
        + BOTTOM_PADDING
    )

    # Don't shrink below original height
    new_height = max(new_height, original_height)

    _update_svg_dimensions(root, new_height, svg_width)

    # ── Step 6: Render SVG → PNG ──
    try:
        import cairosvg

        svg_string = ET.tostring(root, encoding="unicode", xml_declaration=True)
        png_bytes = cairosvg.svg2png(
            bytestring=svg_string.encode("utf-8"),
            output_width=int(svg_width),
            output_height=int(new_height),
        )
        logger.info(f"SVG rendered to PNG: {len(png_bytes)} bytes ({int(svg_width)}x{int(new_height)})")
    except Exception as e:
        logger.exception(f"CairoSVG rendering failed: {e}")
        return None

    # ── Step 7: Place on 1080×1920 canvas ──
    return _compose_on_canvas(png_bytes)


def _compose_on_canvas(card_image_bytes: bytes) -> bytes:
    """
    Place the rendered card onto a 1080×1920 transparent canvas,
    centered with margins so the background video is visible around the edges.
    """
    canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))

    card = Image.open(io.BytesIO(card_image_bytes)).convert("RGBA")

    # Calculate target size (card fills the video minus margins)
    target_w = VIDEO_WIDTH - (CARD_MARGIN * 2)
    target_h = VIDEO_HEIGHT - (CARD_MARGIN * 2)

    # Scale card to fit within target area, maintaining aspect ratio
    card_ratio = card.width / card.height
    target_ratio = target_w / target_h

    if card_ratio > target_ratio:
        new_w = target_w
        new_h = int(new_w / card_ratio)
    else:
        new_h = target_h
        new_w = int(new_h * card_ratio)

    card = card.resize((new_w, new_h), Image.LANCZOS)

    # Center on canvas
    x = (VIDEO_WIDTH - new_w) // 2
    y = (VIDEO_HEIGHT - new_h) // 2

    canvas.paste(card, (x, y), card)

    # Export as PNG
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()
