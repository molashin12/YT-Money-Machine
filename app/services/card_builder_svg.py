"""
SVG Template Card Builder — renders cards from Figma-exported SVG templates.

Workflow:
1. Load the channel's SVG template (assets/channels/<slug>/template.svg)
2. Parse with xml.etree.ElementTree
3. Read the ORIGINAL positions of input_text, main_image, source from the template
4. Inject dynamic content preserving the template's layout
5. Recalculate SVG height based on text length
6. Render to PNG via CairoSVG
7. Place on 1080×1920 transparent canvas

Handles Figma-exported SVG structures:
- Text elements use <tspan> children with their own x/y attributes
- Images use <g> → <rect fill="url(#pattern)"> → <pattern> → <use>/<image> chains
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

# ── Layout constants ──────────────────────────────────────────────────
LINE_HEIGHT = 30                # Pixels between text lines
TEXT_TO_IMAGE_GAP = 20          # Gap between last text line and image
IMAGE_TO_SOURCE_GAP = 16        # Gap between image bottom and source text
BOTTOM_PADDING = 24             # Space below source text
DEFAULT_IMAGE_HEIGHT = 300      # Fallback image height
MIN_CARD_WIDTH = 350            # Minimum card width
MAX_CARD_WIDTH = 800            # Maximum card width
SIDE_PADDING = 24               # Padding on left/right of content

# SVG / XML namespaces
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Register namespaces so ET.tostring doesn't mangle them
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


# ── Utility helpers ───────────────────────────────────────────────────

def _find_by_id(root: ET.Element, elem_id: str) -> Optional[ET.Element]:
    """Find any element by id= attribute, searching the full tree."""
    for el in root.iter():
        if el.get("id") == elem_id:
            return el
    return None


def _get_float(elem: ET.Element, attr: str, default: float = 0) -> float:
    """Safely get a float attribute, stripping 'px' if present."""
    val = elem.get(attr, "")
    if not val:
        return default
    try:
        return float(val.replace("px", "").strip())
    except ValueError:
        return default


def _svg_dims(root: ET.Element) -> tuple[float, float]:
    """Return (width, height) of the SVG root."""
    w = _get_float(root, "width", 0)
    h = _get_float(root, "height", 0)
    # If width/height are missing, try viewBox
    if w == 0 or h == 0:
        vb = root.get("viewBox", "")
        if vb:
            parts = vb.replace(",", " ").split()
            if len(parts) == 4:
                w = float(parts[2])
                h = float(parts[3])
    return w or 500, h or 800


def _wrap_text(text: str, max_chars: int) -> list[str]:
    """Word-wrap text into lines, each ≤ max_chars."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        if len(test) <= max_chars:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _to_base64_uri(image_bytes: bytes) -> str:
    """Convert raw image bytes into a data:image/...;base64,... URI."""
    if image_bytes[:4] == b"\x89PNG":
        mime = "image/png"
    elif image_bytes[:4] == b"RIFF":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _read_tspan_coords(text_el: ET.Element) -> tuple[float, float]:
    """
    Read x/y coordinates from a <text> element, preferring the first <tspan>
    child's coordinates (Figma puts x/y on <tspan>, not the <text> parent).
    """
    # Try the first <tspan> child first
    for child in text_el:
        tag = child.tag
        if isinstance(tag, str) and (tag.endswith("}tspan") or tag == "tspan"):
            x = _get_float(child, "x", 0)
            y = _get_float(child, "y", 0)
            if x > 0 or y > 0:
                return x, y

    # Fall back to <text> element's own attributes
    x = _get_float(text_el, "x", 24)
    y = _get_float(text_el, "y", 120)
    return x, y


# ── Core injection functions ──────────────────────────────────────────

def _inject_text(root: ET.Element, body: str, svg_width: float) -> dict:
    """
    Replace the content of id="input_text" with word-wrapped <tspan> elements.

    Handles Figma exports where x/y live on <tspan> children, not the <text>.

    Returns a dict with:
      x, start_y  — the original position of the text element
      text_height  — total pixel height of the wrapped text block
      num_lines    — number of lines produced
    """
    el = _find_by_id(root, "input_text")
    if el is None:
        logger.warning("id='input_text' not found in SVG")
        return {"x": 24, "start_y": 120, "text_height": LINE_HEIGHT, "num_lines": 1}

    # Read the template's original x / y (prefer tspan coords)
    orig_x, orig_y = _read_tspan_coords(el)

    # Read font-size from the element (Figma usually sets this)
    font_size_str = el.get("font-size", "")
    if not font_size_str:
        # Try style attribute
        style = el.get("style", "")
        m = re.search(r"font-size:\s*(\d+)", style)
        font_size_str = m.group(1) if m else "22"
    font_size = float(font_size_str.replace("px", "").strip()) if font_size_str else 22

    # Calculate chars per line based on the available width and font size
    available_w = svg_width - (orig_x * 2)  # symmetric padding
    # Approximate: each char ≈ 0.55 × font_size wide
    chars_per_line = max(15, int(available_w / (font_size * 0.55)))
    line_height = font_size * 1.35  # reasonable line-height

    logger.info(f"SVG text: x={orig_x}, y={orig_y}, font={font_size}px, "
                f"available_w={available_w}, chars/line={chars_per_line}")

    # Clear all existing content
    el.text = None
    el.tail = None
    for child in list(el):
        el.remove(child)

    # Wrap and create <tspan> elements
    lines = _wrap_text(body, chars_per_line)
    for i, line in enumerate(lines):
        tspan = ET.SubElement(el, f"{{{SVG_NS}}}tspan")
        tspan.set("x", str(orig_x))
        if i == 0:
            # First line stays at the original y
            tspan.set("y", str(orig_y))
        else:
            tspan.set("dy", str(line_height))
        tspan.text = line

    text_height = len(lines) * line_height
    logger.info(f"SVG text wrapped: {len(lines)} lines, {text_height:.0f}px total")

    return {
        "x": orig_x,
        "start_y": orig_y,
        "text_height": text_height,
        "num_lines": len(lines),
        "line_height": line_height,
    }


def _inject_image(
    root: ET.Element,
    image_bytes: Optional[bytes],
    new_y: float,
    svg_width: float,
) -> dict:
    """
    Replace the image inside id="main_image" with a base64 data URI.
    Reposition the image rect to new_y.

    Handles Figma's pattern-based structure:
      <g id="main_image">
        <rect x="..." y="..." width="..." height="..." fill="url(#patternId)" />
      </g>
      ...in <defs>...
      <pattern id="patternId">
        <use xlink:href="#imageId" transform="scale(...)"/>
      </pattern>
      <image id="imageId" xlink:href="data:image/..." width="..." height="..."/>

    Also handles simple <image id="main_image" .../> elements.

    Returns dict with img_width, img_height, img_x, img_y.
    """
    el = _find_by_id(root, "main_image")
    if el is None:
        logger.warning("id='main_image' not found in SVG")
        return {"img_width": svg_width * 0.9, "img_height": DEFAULT_IMAGE_HEIGHT,
                "img_x": svg_width * 0.05, "img_y": new_y}

    tag_local = el.tag.split("}")[-1] if "}" in el.tag else el.tag

    if tag_local == "g":
        # ── Figma pattern-based structure ──
        return _inject_image_figma_group(root, el, image_bytes, new_y, svg_width)
    else:
        # ── Simple <image> element ──
        return _inject_image_simple(el, image_bytes, new_y, svg_width)


def _inject_image_figma_group(
    root: ET.Element,
    group_el: ET.Element,
    image_bytes: Optional[bytes],
    new_y: float,
    svg_width: float,
) -> dict:
    """Handle Figma's <g> → <rect fill=url(#pattern)> → <pattern> → <image> chain."""
    # Find the <rect> child of the group
    rect_el = None
    for child in group_el:
        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if child_tag == "rect":
            rect_el = child
            break

    if rect_el is None:
        logger.warning("No <rect> found inside main_image <g>")
        return {"img_width": svg_width * 0.9, "img_height": DEFAULT_IMAGE_HEIGHT,
                "img_x": svg_width * 0.05, "img_y": new_y, "natural_width": svg_width * 0.9}

    # Read the rect's original dimensions
    img_x = _get_float(rect_el, "x", svg_width * 0.05)
    img_w = _get_float(rect_el, "width", svg_width * 0.9)
    img_h = _get_float(rect_el, "height", DEFAULT_IMAGE_HEIGHT)
    natural_w = img_w  # track what width the image naturally wants

    if image_bytes:
        # Get actual image dimensions for proportional sizing
        try:
            pil = Image.open(io.BytesIO(image_bytes))
            actual_w, actual_h = pil.width, pil.height
            aspect = actual_w / actual_h
            # Compute proportional height to show full image at rect width
            img_h = img_w / aspect
            # Track what width the image would need at its natural aspect
            natural_w = actual_w
        except Exception:
            pass

        # Update rect height to match proportional image
        rect_el.set("height", str(img_h))

        # Extract the pattern ID from fill="url(#patternId)"
        fill_attr = rect_el.get("fill", "")
        pattern_match = re.search(r"url\(#([^)]+)\)", fill_attr)

        if pattern_match:
            pattern_id = pattern_match.group(1)
            _replace_pattern_image(root, pattern_id, image_bytes, img_w, img_h)
        else:
            # No pattern fill — convert the rect+group into a direct <image>
            logger.info("No pattern fill found on rect, converting to direct <image>")
            _convert_group_to_image(group_el, rect_el, image_bytes, img_w, img_h)

    # Move the rect to the new Y position
    rect_el.set("y", str(new_y))

    logger.info(f"SVG image (Figma group): x={img_x}, y={new_y}, w={img_w}, h={img_h:.0f}")
    return {"img_width": img_w, "img_height": img_h, "img_x": img_x, "img_y": new_y,
            "natural_width": natural_w}


def _replace_pattern_image(
    root: ET.Element,
    pattern_id: str,
    image_bytes: bytes,
    rect_w: float,
    rect_h: float,
) -> None:
    """
    Find the <pattern> by ID, locate the <image> it references,
    and replace the image data + recalculate the pattern transform scale.
    """
    pattern_el = _find_by_id(root, pattern_id)
    if pattern_el is None:
        logger.warning(f"Pattern '{pattern_id}' not found in SVG defs")
        return

    # Inside the pattern, find <use xlink:href="#imageId"> to get the image ID
    image_id = None
    use_el = None
    for child in pattern_el:
        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if child_tag == "use":
            use_el = child
            href = child.get(f"{{{XLINK_NS}}}href", "") or child.get("href", "")
            if href.startswith("#"):
                image_id = href[1:]
            break

    # If no <use>, look for a direct <image> inside the pattern
    if image_id is None:
        for child in pattern_el:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag == "image":
                # Direct image inside pattern — replace its href
                data_uri = _to_base64_uri(image_bytes)
                child.set(f"{{{XLINK_NS}}}href", data_uri)
                child.set("href", data_uri)
                # Update dimensions and recalc scale
                _update_pattern_scale(pattern_el, child, image_bytes, rect_w, rect_h)
                logger.info("Replaced direct <image> inside pattern")
                return

    if image_id is None:
        logger.warning(f"No <use> or <image> found inside pattern '{pattern_id}'")
        return

    # Find the referenced <image> in <defs>
    image_el = _find_by_id(root, image_id)
    if image_el is None:
        logger.warning(f"Image element '{image_id}' not found in SVG defs")
        return

    # Replace the image data
    data_uri = _to_base64_uri(image_bytes)
    image_el.set(f"{{{XLINK_NS}}}href", data_uri)
    image_el.set("href", data_uri)

    # Get the actual image dimensions to recalculate the pattern scale
    try:
        pil = Image.open(io.BytesIO(image_bytes))
        actual_w, actual_h = pil.width, pil.height
    except Exception:
        actual_w = _get_float(image_el, "width", 1000)
        actual_h = _get_float(image_el, "height", 600)

    # Update the <image> width/height in defs
    image_el.set("width", str(actual_w))
    image_el.set("height", str(actual_h))

    # Recalculate the pattern's <use> transform scale
    # Pattern uses objectBoundingBox content units, so scale factors map
    # the image pixel dimensions into the 0..1 range of the pattern
    scale_x = 1.0 / actual_w
    scale_y = 1.0 / actual_h
    if use_el is not None:
        use_el.set("transform", f"scale({scale_x:.8f} {scale_y:.8f})")
        logger.info(f"Pattern scale updated: {scale_x:.8f} × {scale_y:.8f} "
                    f"(image {actual_w}×{actual_h})")

    # Also set preserveAspectRatio on the image for better display
    image_el.set("preserveAspectRatio", "none")


def _update_pattern_scale(
    pattern_el: ET.Element,
    image_el: ET.Element,
    image_bytes: bytes,
    rect_w: float,
    rect_h: float,
) -> None:
    """Update a direct <image> inside a pattern and its transform."""
    try:
        pil = Image.open(io.BytesIO(image_bytes))
        actual_w, actual_h = pil.width, pil.height
    except Exception:
        actual_w = _get_float(image_el, "width", 1000)
        actual_h = _get_float(image_el, "height", 600)

    image_el.set("width", str(actual_w))
    image_el.set("height", str(actual_h))
    image_el.set("preserveAspectRatio", "none")

    # If pattern uses objectBoundingBox, recalc transform
    content_units = pattern_el.get("patternContentUnits", "")
    if content_units == "objectBoundingBox":
        scale_x = 1.0 / actual_w
        scale_y = 1.0 / actual_h
        image_el.set("transform", f"scale({scale_x:.8f} {scale_y:.8f})")


def _convert_group_to_image(
    group_el: ET.Element,
    rect_el: ET.Element,
    image_bytes: bytes,
    width: float,
    height: float,
) -> None:
    """Convert a <g>+<rect> into a direct <image> element as fallback."""
    data_uri = _to_base64_uri(image_bytes)
    x = rect_el.get("x", "0")
    y = rect_el.get("y", "0")
    rx = rect_el.get("rx", "0")

    # Create a new <image> element
    img = ET.SubElement(group_el, f"{{{SVG_NS}}}image")
    img.set("x", x)
    img.set("y", y)
    img.set("width", str(width))
    img.set("height", str(height))
    img.set("href", data_uri)
    img.set(f"{{{XLINK_NS}}}href", data_uri)
    img.set("preserveAspectRatio", "xMidYMid meet")
    if float(rx) > 0:
        # Clip to rounded rect via clip-path would be needed, but skip for simplicity
        pass

    # Remove the rect
    group_el.remove(rect_el)


def _inject_image_simple(
    el: ET.Element,
    image_bytes: Optional[bytes],
    new_y: float,
    svg_width: float,
) -> dict:
    """Handle a simple <image id="main_image" .../> element."""
    img_x = _get_float(el, "x", svg_width * 0.05)
    img_w = _get_float(el, "width", svg_width * 0.9)
    img_h = _get_float(el, "height", DEFAULT_IMAGE_HEIGHT)
    natural_w = img_w

    if image_bytes:
        data_uri = _to_base64_uri(image_bytes)
        el.set("href", data_uri)
        el.set(f"{{{XLINK_NS}}}href", data_uri)

        # Calculate proportional height from the actual image
        try:
            pil = Image.open(io.BytesIO(image_bytes))
            aspect = pil.width / pil.height
            img_h = img_w / aspect
            natural_w = pil.width
        except Exception:
            pass

        el.set("width", str(img_w))
        el.set("height", str(img_h))
        el.set("preserveAspectRatio", "xMidYMid meet")

    # Move to new Y position
    el.set("y", str(new_y))

    logger.info(f"SVG image (simple): x={img_x}, y={new_y}, w={img_w}, h={img_h:.0f}")
    return {"img_width": img_w, "img_height": img_h, "img_x": img_x, "img_y": new_y,
            "natural_width": natural_w}


def _inject_source(root: ET.Element, source_text: str, new_y: float, x: float = None) -> None:
    """
    Replace text of id='source' and reposition it.

    Handles Figma exports where coordinates are on <tspan> children.
    """
    el = _find_by_id(root, "source")
    if el is None:
        logger.warning("id='source' not found in SVG")
        return

    # Read original x from the template (prefer tspan coords)
    if x is None:
        orig_x, _ = _read_tspan_coords(el)
        x = orig_x if orig_x > 0 else 24

    # Clear all existing content
    el.text = None
    el.tail = None
    for child in list(el):
        el.remove(child)

    # Create a new <tspan> with the proper coordinates
    tspan = ET.SubElement(el, f"{{{SVG_NS}}}tspan")
    tspan.set("x", str(x))
    tspan.set("y", str(new_y))
    tspan.text = source_text

    logger.info(f"SVG source: '{source_text}' at x={x}, y={new_y}")


def _resize_svg(root: ET.Element, svg_width: float, new_height: float) -> None:
    """Update SVG root height, viewBox, and any background <rect>."""
    root.set("width", str(int(svg_width)))
    root.set("height", str(int(new_height)))
    root.set("viewBox", f"0 0 {int(svg_width)} {int(new_height)}")

    # Expand background rect(s) — typically the first <rect> with full width
    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        rw = _get_float(rect, "width", 0)
        if rw >= svg_width * 0.9:
            # Only expand the background rect (top-level, no pattern fill)
            fill = rect.get("fill", "")
            if not fill.startswith("url("):
                rect.set("height", str(int(new_height)))
                logger.info(f"Background rect height → {int(new_height)}")
                break


# ── Main entry point ──────────────────────────────────────────────────

async def build_card_svg(
    channel: ChannelConfig,
    title: str,
    body: str,
    related_image: Optional[bytes] = None,
    image_source: str = "",
) -> Optional[bytes]:
    """
    Build a card by injecting content into the channel's SVG template.

    The card dynamically sizes to fit content:
    - Image area expands to show the full image (no cropping)
    - Width adapts based on image proportions (clamped to min/max)
    - Height grows to fit text + image + source

    Returns PNG bytes on a 1080×1920 transparent canvas, or None on failure.
    """
    logger.info(f"Building SVG card for channel: {channel.name}")

    svg_path = channel.svg_template_path
    if not svg_path or not Path(svg_path).exists():
        logger.error(f"No SVG template for '{channel.slug}' at: {svg_path}")
        return None

    # ── Parse SVG ──
    try:
        svg_raw = Path(svg_path).read_text(encoding="utf-8")
        root = ET.fromstring(svg_raw)
    except Exception as e:
        logger.error(f"SVG parse error: {e}")
        return None

    svg_w, original_h = _svg_dims(root)

    # ── 1. Inject text ──
    text_info = _inject_text(root, body, svg_w)
    text_bottom = text_info["start_y"] + text_info["text_height"]

    # ── 2. Inject image ──
    image_y = text_bottom + TEXT_TO_IMAGE_GAP
    img_info = _inject_image(root, related_image, image_y, svg_w)
    image_bottom = img_info["img_y"] + img_info["img_height"]

    # ── 3. Inject source ──
    source_text = image_source if image_source else "source: web"
    source_y = image_bottom + IMAGE_TO_SOURCE_GAP
    _inject_source(root, source_text, source_y, x=text_info["x"])

    # ── 4. Compute dynamic dimensions ──
    new_height = source_y + BOTTOM_PADDING + 10
    new_height = max(new_height, original_h)

    # Dynamic width: if the image has a wide natural aspect, widen the card
    final_w = svg_w
    # Only widen if the image's natural proportions suggest it
    if related_image and img_info.get("natural_width", 0) > 0:
        try:
            pil = Image.open(io.BytesIO(related_image))
            img_aspect = pil.width / pil.height
            # If image is very wide (landscape), widen the card
            if img_aspect > 1.2:
                # Content area = card width minus padding
                content_w = img_info["img_width"]
                desired_w = content_w + (SIDE_PADDING * 2)
                final_w = max(svg_w, min(desired_w, MAX_CARD_WIDTH))
                final_w = max(final_w, MIN_CARD_WIDTH)
        except Exception:
            pass

    _resize_svg(root, final_w, new_height)

    # ── 5. Render → PNG ──
    try:
        import cairosvg

        svg_str = ET.tostring(root, encoding="unicode", xml_declaration=True)
        png_data = cairosvg.svg2png(
            bytestring=svg_str.encode("utf-8"),
            output_width=int(final_w),
            output_height=int(new_height),
        )
        logger.info(f"SVG → PNG: {len(png_data)} bytes, {int(final_w)}×{int(new_height)}")
    except Exception as e:
        logger.exception(f"CairoSVG render failed: {e}")
        return None

    # ── 6. Compose on 1080×1920 canvas ──
    return _compose_on_canvas(png_data)


def _compose_on_canvas(card_bytes: bytes) -> bytes:
    """Place rendered card on a 1080×1920 transparent canvas, centered."""
    canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    card = Image.open(io.BytesIO(card_bytes)).convert("RGBA")

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

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
