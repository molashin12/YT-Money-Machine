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
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from PIL import Image

from app.config import (
    ChannelConfig,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    CARD_MARGIN,
    FONTS_DIR,
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
MAX_TEXT_LINES = 10              # Maximum text lines before shrinking font
MIN_FONT_SIZE = 14              # Minimum font size (px)
MAX_IMAGE_HEIGHT_RATIO = 0.6    # Image can use at most 60% of card height

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
    """Get the standard width and height from viewBox or width/height attributes."""
    try:
        w = float(root.get("width", "0").replace("px", ""))
        h = float(root.get("height", "0").replace("px", ""))
        if w > 0 and h > 0:
            return w, h
    except:
        pass

    viewBox = root.get("viewBox")
    if viewBox:
        parts = viewBox.split()
        if len(parts) == 4:
            return float(parts[2]), float(parts[3])
    return 320.0, 500.0


def _embed_font(root: ET.Element):
    """
    Embed the Inter font as a base64 Data URI inside the SVG to fix font width variations across OS.
    If 'Inter' font is missing on an AWS Linux server, text renders wider and overlaps static checkmarks.
    """
    font_path = FONTS_DIR / "Inter.ttf"
    if not font_path.exists():
        logger.warning(f"Font not found at {font_path}, cannot embed font inline.")
        return
    
    try:
        font_bytes = font_path.read_bytes()
        b64_font = base64.b64encode(font_bytes).decode("utf-8")
        
        defs = root.find(f"{{{SVG_NS}}}defs")
        if defs is None:
            defs = ET.Element(f"{{{SVG_NS}}}defs")
            root.insert(0, defs)
            
        style = ET.SubElement(defs, f"{{{SVG_NS}}}style")
        style.text = f"""
        @font-face {{
            font-family: 'Inter';
            src: url(data:font/ttf;base64,{b64_font}) format('truetype');
            font-weight: normal;
            font-style: normal;
        }}
        @font-face {{
            font-family: 'Inter';
            src: url(data:font/ttf;base64,{b64_font}) format('truetype');
            font-weight: bold;
            font-style: normal;
        }}
        """
        logger.info("Embedded Inter font as Base64 in SVG defs.")
    except Exception as e:
        logger.error(f"Failed to embed font: {e}")


def _fix_figma_patterns_for_cairosvg(root: ET.Element):
    """
    Figma uses <pattern> fills for the Profile Avatar.
    CairoSVG frequently fails to render these or leaves them corrupted.
    We parse the SVG, find any shapes using a pattern that has an image, and rewrite them 
    into <image clip-path="..."> elements which CairoSVG handles perfectly.
    """
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        return

    pattern_to_image = {}
    for pat in root.findall(f".//{{{SVG_NS}}}pattern"):
        pat_id = pat.get("id")
        
        use_el = pat.find(f"{{{SVG_NS}}}use")
        href = None
        
        if use_el is not None:
            href_ref = use_el.get(f"{{{XLINK_NS}}}href") or use_el.get("href")
            if href_ref and href_ref.startswith("#"):
                ref_img = _find_by_id(root, href_ref[1:])
                if ref_img is not None:
                    href = ref_img.get(f"{{{XLINK_NS}}}href") or ref_img.get("href")
        
        if not href:
            image_el = pat.find(f"{{{SVG_NS}}}image")
            if image_el is not None:
                href = image_el.get(f"{{{XLINK_NS}}}href") or image_el.get("href")
                
        if pat_id and href:
            pattern_to_image[f"url(#{pat_id})"] = href

    if not pattern_to_image:
        return

    for parent in list(root.iter()):
        for child in list(parent):
            fill = child.get("fill", "")
            if fill in pattern_to_image:
                img_href = pattern_to_image[fill]
                shape_tag = child.tag.split("}")[-1]
                
                if shape_tag not in ("circle", "rect", "ellipse"):
                    continue

                clip_id = f"clip_{uuid.uuid4().hex[:8]}"
                clip_path = ET.Element(f"{{{SVG_NS}}}clipPath", {"id": clip_id})
                
                shape_copy = ET.Element(child.tag, child.attrib)
                if "fill" in shape_copy.attrib:
                    del shape_copy.attrib["fill"]
                if "id" in shape_copy.attrib:
                    del shape_copy.attrib["id"] 
                clip_path.append(shape_copy)
                defs.append(clip_path)

                x, y, w, h = "0", "0", "100", "100"
                if shape_tag == "circle":
                    cx = float(child.get("cx", 0))
                    cy = float(child.get("cy", 0))
                    r = float(child.get("r", 0))
                    x = str(cx - r)
                    y = str(cy - r)
                    w = str(r * 2)
                    h = str(r * 2)
                elif shape_tag == "ellipse":
                    cx = float(child.get("cx", 0))
                    cy = float(child.get("cy", 0))
                    rx = float(child.get("rx", 0))
                    ry = float(child.get("ry", 0))
                    x = str(cx - rx)
                    y = str(cy - ry)
                    w = str(rx * 2)
                    h = str(ry * 2)
                elif shape_tag == "rect":
                    x = child.get("x", "0")
                    y = child.get("y", "0")
                    w = child.get("width", "100")
                    h = child.get("height", "100")

                g = ET.Element(f"{{{SVG_NS}}}g")
                if "id" in child.attrib:
                    g.set("id", child.get("id"))
                    
                ET.SubElement(g, f"{{{SVG_NS}}}image", {
                    "x": x,
                    "y": y,
                    "width": w,
                    "height": h,
                    "href": img_href,
                    "preserveAspectRatio": "xMidYMid slice",
                    "clip-path": f"url(#{clip_id})"
                })
                
                idx = list(parent).index(child)
                parent.insert(idx, g)
                parent.remove(child)
                logger.info(f"Fixed Figma pattern fill on <{shape_tag}> -> <image clip-path>")


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


def _estimate_text_width(text: str, font_size: float, is_bold: bool = False) -> float:
    """Exact pixel width measurement using PIL if Inter font is available; fallback to heuristic."""
    try:
        from PIL import ImageFont, ImageDraw, Image
        font_path = FONTS_DIR / ("Inter-Bold.ttf" if is_bold else "Inter.ttf")
        if not font_path.exists():
            font_path = FONTS_DIR / "Inter.ttf"  # Fallback to regular if bold missing
            
        if font_path.exists():
            font = ImageFont.truetype(str(font_path), int(font_size))
            dummy_img = Image.new("RGB", (1, 1))
            draw = ImageDraw.Draw(dummy_img)
            bbox = draw.textbbox((0, 0), text, font=font)
            return float(bbox[2] - bbox[0])
    except Exception as e:
        logger.warning(f"PIL font width measurement failed, using heuristic: {e}")
        pass

    # Fallback heuristic
    char_width = 0.0
    for c in text:
        if c.isupper(): char_width += 0.65
        elif c in "ilI1.,|/;:()[]{}!'\"": char_width += 0.25
        elif c.islower(): char_width += 0.55
        elif c.isdigit(): char_width += 0.55
        elif c.isspace(): char_width += 0.35
        elif c in "mW": char_width += 0.8
        else: char_width += 0.55
    
    # Apply a larger multiplier for bold font in fallback
    if is_bold:
        char_width *= 1.15
        
    return char_width * font_size


def _reposition_checkmark(root: ET.Element) -> None:
    """
    Read the channel name text that is ALREADY in the template (set in Figma),
    compute its pixel width, and translate the 'Check' group so it appears
    right after the name with a small gap — without touching any static content.
    """
    name_group = _find_by_id(root, "Name")
    if name_group is None:
        return

    # Find the <text> element inside the Name group
    text_el = None
    for child in name_group:
        if child.tag.endswith("text"):
            text_el = child
            break
    if text_el is None:
        return

    # Get the tspan and read the current name text + x position
    tspan = None
    for child in text_el:
        if child.tag.endswith("tspan"):
            tspan = child
            break

    name_text = (tspan.text if tspan is not None and tspan.text else text_el.text) or ""
    if not name_text:
        return

    name_x = 0.0
    if tspan is not None:
        name_x = float(tspan.get("x", "0"))
    else:
        name_x = float(text_el.get("x", "0"))

    fs = _get_float(text_el, "font-size", 12)
    font_weight = text_el.get("font-weight", "") + text_el.get("style", "")
    is_bold = "bold" in font_weight or "700" in font_weight

    name_width = _estimate_text_width(name_text, fs, is_bold)
    name_right_edge = name_x + name_width  # pixel x where name text ends

    # Find the Check element
    check_el = _find_by_id(root, "Check")
    if check_el is None:
        # Try inside the Name group
        check_el = _find_by_id(name_group, "Check")
    if check_el is None:
        return

    # Get the current leftmost x of the checkmark SVG path
    # The Check group contains a <path> whose d= attribute has the coordinates.
    # We parse the first number pair from the path to find the original x position.
    check_min_x = None
    for path in check_el.iter(f"{{{SVG_NS}}}path"):
        d = path.get("d", "")
        # Extract all numbers from the path data
        nums = re.findall(r"[\d.]+", d)
        if nums:
            check_min_x = float(nums[0])  # first x coordinate in the path
            break

    if check_min_x is None:
        return

    GAP_PX = 10  # comfortable gap between name and checkmark
    desired_x = name_right_edge + GAP_PX
    shift = desired_x - check_min_x

    if abs(shift) < 1:
        logger.info(f"Checkmark already correctly positioned (shift={shift:.1f}px)")
        return

    # Apply translation to the Check group
    curr_transform = check_el.get("transform", "")
    new_transform = f"{curr_transform} translate({shift:.1f} 0)".strip()
    check_el.set("transform", new_transform)
    logger.info(f"Repositioned checkmark: name='{name_text}' width={name_width:.1f}px, "
                f"shift={shift:.1f}px")


# ── Core injection functions ──────────────────────────────────────────

def _compute_text_layout(
    body: str, svg_width: float, text_x: float, font_size: float,
) -> dict:
    """
    Dry-run text layout: compute how many lines and how much height
    the text would need at the given font size and card width.
    Does NOT modify the SVG — used for planning.
    """
    available_w = svg_width - (text_x * 2)
    chars_per_line = max(10, int(available_w / (font_size * 0.55)))
    line_height = font_size * 1.35
    lines = _wrap_text(body, chars_per_line)
    text_height = len(lines) * line_height
    return {
        "num_lines": len(lines),
        "text_height": text_height,
        "line_height": line_height,
        "chars_per_line": chars_per_line,
        "font_size": font_size,
    }


def _inject_text(
    root: ET.Element, body: str, svg_width: float,
    font_size_override: float = 0,
) -> dict:
    """
    Replace the content of id="input_text" with word-wrapped <tspan> elements.

    If font_size_override > 0, use it instead of the template's font size.
    This allows the orchestrator to shrink text dynamically.

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
    template_font = float(font_size_str.replace("px", "").strip()) if font_size_str else 22

    # Use override if provided
    font_size = font_size_override if font_size_override > 0 else template_font

    # Calculate chars per line based on the available width and font size
    available_w = svg_width - (orig_x * 2)  # symmetric padding
    # Approximate: each char ≈ 0.55 × font_size wide
    chars_per_line = max(10, int(available_w / (font_size * 0.55)))
    line_height = font_size * 1.35  # reasonable line-height

    logger.info(f"SVG text: x={orig_x}, y={orig_y}, font={font_size}px, "
                f"available_w={available_w}, chars/line={chars_per_line}")

    # Apply the font size to the element
    el.set("font-size", str(font_size))
    # Also update style attribute if it has font-size
    style = el.get("style", "")
    if "font-size" in style:
        style = re.sub(r"font-size:\s*\d+(\.\d+)?px?", f"font-size:{font_size}px", style)
        el.set("style", style)

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
        "font_size": font_size,
    }


def _inject_image(
    root: ET.Element,
    image_bytes: Optional[bytes],
    new_y: float,
    svg_width: float,
    original_svg_width: float = 0,
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
        return _inject_image_figma_group(root, el, image_bytes, new_y, svg_width, original_svg_width)
    else:
        # ── Simple <image> element ──
        return _inject_image_simple(el, image_bytes, new_y, svg_width, original_svg_width)


def _inject_image_figma_group(
    root: ET.Element,
    group_el: ET.Element,
    image_bytes: Optional[bytes],
    new_y: float,
    svg_width: float,
    original_svg_width: float = 0,
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
    width_diff = svg_width - original_svg_width if original_svg_width else 0
    img_w = _get_float(rect_el, "width", svg_width * 0.9) + width_diff
    rect_el.set("width", str(img_w))
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
    original_svg_width: float = 0,
) -> dict:
    """Handle a simple <image id="main_image" .../> element."""
    img_x = _get_float(el, "x", svg_width * 0.05)
    width_diff = svg_width - original_svg_width if original_svg_width else 0
    img_w = _get_float(el, "width", svg_width * 0.9) + width_diff
    el.set("width", str(img_w))
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


def _resize_svg(root, svg_width, new_height, original_width=0):
    """Update SVG dimensions and expand ALL background rects to cover full card."""
    SVG_NS = "http://www.w3.org/2000/svg"
    root.set("width", str(int(svg_width)))
    root.set("height", str(int(new_height)))
    root.set("viewBox", f"0 0 {int(svg_width)} {int(new_height)}")

    width_diff = svg_width - original_width

    # Find and expand background rects.
    # Strategy: any rect whose fill is NOT a pattern URL is a candidate.
    # The background rect is typically the one with the largest area.
    best_rect = None
    best_area = 0
    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        fill = rect.get("fill", "")
        if fill.startswith("url("):
            continue  # skip image pattern rects
        try:
            rw = float(rect.get("width", "0").replace("px", ""))
            rh = float(rect.get("height", "0").replace("px", ""))
        except ValueError:
            continue
        area = rw * rh
        if area > best_area:
            best_area = area
            best_rect = rect

    for rect in root.iter(f"{{{SVG_NS}}}rect"):
        fill = rect.get("fill", "")
        if fill.startswith("url("):
            continue
        try:
            rw = float(rect.get("width", "0").replace("px", ""))
        except ValueError:
            continue
        
        if rw >= original_width * 0.8 and rect != best_rect:
            rect.set("width", str(int(rw + width_diff)))

    if best_rect is not None:
        best_rect.set("width", str(int(svg_width)))
        best_rect.set("height", str(int(new_height)))
        logger.info(f"Background rect -> {int(svg_width)}x{int(new_height)}")

    if width_diff > 0:
        more_el = _find_by_id(root, "More")
        if more_el is not None:
            current_transform = more_el.get("transform", "")
            more_el.set("transform", f"{current_transform} translate({width_diff} 0)".strip())


def _inject_channel_info(root: ET.Element, channel: ChannelConfig) -> None:
    """
    Inject channel info (Avatar, Name, Handle) and reposition Checkmark if Name length changes.
    """
    # 1. Update Avatar
    avatar_el = _find_by_id(root, "Avatar")
    if avatar_el is not None and channel.logo_path:
        logo_path = Path(channel.logo_path)
        if logo_path.exists():
            try:
                # Find the element with a fill="url(#pattern...)" inside Avatar
                for child in avatar_el.iter():
                    fill = child.get("fill", "")
                    if fill.startswith("url(#"):
                        pattern_id = fill[5:-1]
                        image_bytes = logo_path.read_bytes()
                        rect_w = (_get_float(child, "r", 16) * 2) or _get_float(child, "width", 32)
                        rect_h = rect_w
                        _replace_pattern_image(root, pattern_id, image_bytes, rect_w, rect_h)
                        break
            except Exception as e:
                logger.error(f"Failed to inject avatar: {e}")

    # 2. Update Name and push Checkmark
    name_el = _find_by_id(root, "Name")
    if name_el is not None and channel.name:
        text_el = None
        for child in name_el:
            if child.tag.endswith("text"):
                text_el = child
                break
        
        if text_el is not None:
            tspan = None
            for child in text_el:
                if child.tag.endswith("tspan"):
                    tspan = child
                    break
            
            orig_text = tspan.text if tspan is not None and tspan.text else text_el.text or ""
            new_text = channel.name

            fs = _get_float(text_el, "font-size", 12)
            font_weight = text_el.get("font-weight", "") + text_el.get("style", "")
            is_bold = "bold" in font_weight or "700" in font_weight

            if tspan is not None:
                tspan.text = new_text
            else:
                text_el.text = new_text

            if orig_text:
                orig_w = _estimate_text_width(orig_text, fs, is_bold)
                new_w = _estimate_text_width(new_text, fs, is_bold)
                diff = new_w - orig_w

                # Find checkmark and adjust position
                check_el = _find_by_id(name_el, "Check")
                if check_el is not None:
                    curr_transform = check_el.get("transform", "")
                    check_el.set("transform", f"{curr_transform} translate({diff} 0)".strip())
    
    # 3. Update Handle (username)
    handle_el = None
    for node in root.iter():
        if node.get("id", "").startswith("@"):
            handle_el = node
            break
            
    if handle_el is not None and channel.slug:
        if handle_el.tag.endswith("text"):
            tspan = handle_el.find("{http://www.w3.org/2000/svg}tspan")
            if tspan is not None:
                tspan.text = f"@{channel.slug}"
            else:
                handle_el.text = f"@{channel.slug}"
        else:
            # It's a group <g>. Figma sometimes exports the @ character and the handle as separate <text> nodes.
            # We want to replace the first one and delete the rest so as to not leave a trailing overlap.
            texts = list(handle_el.findall(".//{http://www.w3.org/2000/svg}text"))
            if texts:
                first_text = texts[0]
                tspan = first_text.find("{http://www.w3.org/2000/svg}tspan")
                if tspan is not None:
                    tspan.text = f"@{channel.slug}"
                else:
                    first_text.text = f"@{channel.slug}"
                    
                parent_map = {c: p for p in handle_el.iter() for c in p}
                for extra in texts[1:]:
                    p = parent_map.get(extra)
                    if p is not None:
                        p.remove(extra)


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

    Two-pass dynamic layout:
    Pass 1: Compute optimal font size and card width to fit text (text is ALWAYS prioritized)
    Pass 2: Inject content with the computed parameters

    The card adapts to content:
    - Text is NEVER cut off — font size shrinks and/or card widens to fit
    - Image area shows the full image proportionally (no cropping)
    - Card width and height are both dynamic

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

    # ═════════════════════════════════════════════════════════════════════
    # PASS 1: Determine optimal font size and card width so text fits
    # ═════════════════════════════════════════════════════════════════════

    # Read the template's original text position and font size
    text_el = _find_by_id(root, "input_text")
    text_x = SIDE_PADDING
    template_font = 22.0
    if text_el is not None:
        text_x, _ = _read_tspan_coords(text_el)
        fs = text_el.get("font-size", "")
        if not fs:
            style = text_el.get("style", "")
            m = re.search(r"font-size:\s*(\d+)", style)
            fs = m.group(1) if m else "22"
        template_font = float(fs.replace("px", "").strip()) if fs else 22.0

    # Strategy: Keep original template width (vertical video friendly).
    # Allow text to wrap into more lines (grow height, not width).
    # Only shrink font if text would exceed MAX_TEXT_LINES.
    best_font = template_font
    best_width = svg_w  # keep original width

    layout = _compute_text_layout(body, svg_w, text_x, template_font)
    if layout["num_lines"] <= MAX_TEXT_LINES:
        logger.info(f"Text fits at template size: {layout['num_lines']} lines, "
                    f"font={template_font}px")
    else:
        # Only shrink font if too many lines at original width
        found = False
        for try_font in range(int(template_font) - 1, int(MIN_FONT_SIZE) - 1, -1):
            layout = _compute_text_layout(body, svg_w, text_x, float(try_font))
            if layout["num_lines"] <= MAX_TEXT_LINES:
                best_font = float(try_font)
                found = True
                logger.info(f"Text fits with smaller font: {layout['num_lines']} lines, "
                            f"font={best_font}px")
                break
        
        # If shrinking to MIN_FONT_SIZE wasn't enough, expand the width of the card
        if not found:
            best_font = MIN_FONT_SIZE
            for try_width in range(int(svg_w) + 10, int(MAX_CARD_WIDTH) + 1, 10):
                layout = _compute_text_layout(body, float(try_width), text_x, MIN_FONT_SIZE)
                if layout["num_lines"] <= MAX_TEXT_LINES:
                    best_width = float(try_width)
                    found = True
                    logger.info(f"Text fits with wider card: width={best_width}px, "
                                f"font={best_font}px")
                    break
            
            if not found:
                best_width = MAX_CARD_WIDTH
                layout = _compute_text_layout(body, best_width, text_x, MIN_FONT_SIZE)
                logger.warning(f"Text STILL long — using max width {MAX_CARD_WIDTH}px and min font {MIN_FONT_SIZE}px")

    logger.info(f"Dynamic layout decided: width={best_width:.0f}, font={best_font}px "
                f"(template: {svg_w:.0f}w, {template_font}px)")

    # ═════════════════════════════════════════════════════════════════════
    # PASS 2: Inject content with the computed parameters
    # ═════════════════════════════════════════════════════════════════════

    # ── 1. Inject text with the optimal font size ──
    text_info = _inject_text(root, body, best_width, font_size_override=best_font)
    text_bottom = text_info["start_y"] + text_info["text_height"]

    # ── 2. Inject image ──
    image_y = text_bottom + TEXT_TO_IMAGE_GAP
    img_info = _inject_image(root, related_image, image_y, best_width, original_svg_width=svg_w)
    image_bottom = img_info["img_y"] + img_info["img_height"]

    # ── 3. Inject source ──
    source_text = image_source if image_source else "source: web"
    source_y = image_bottom + IMAGE_TO_SOURCE_GAP
    _inject_source(root, source_text, source_y, x=text_info["x"])

    # ── X. Reposition checkmark badge to sit right after the channel name ──
    # We don't overwrite Figma's static name/avatar/handle, but we DO need
    # to shift the checkmark so it doesn't overlap a name of different length.
    _reposition_checkmark(root)

    # ── 4. Compute final dimensions ──
    new_height = source_y + BOTTOM_PADDING + 10
    new_height = max(new_height, original_h)

    # ── 5. Apply Core Fixes for CairoSVG / Linux Rendering ──
    _embed_font(root)
    _fix_figma_patterns_for_cairosvg(root)

    _resize_svg(root, best_width, new_height, original_width=svg_w)
    
    # ── 6. Ensure all images have 'href' for CairoSVG compat ──
    for img in root.iter(f"{{{SVG_NS}}}image"):
        xh = img.get(f"{{{XLINK_NS}}}href", "")
        if xh and not img.get("href"):
            img.set("href", xh)

    with open("test_output.svg", "wb") as f:
        f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))

    # ── 5. Render → PNG ──
    try:
        import cairosvg
        svg_str = ET.tostring(root, encoding="unicode", xml_declaration=True)
        png_data = cairosvg.svg2png(
            bytestring=svg_str.encode("utf-8"),
            output_width=int(best_width),
            output_height=int(new_height),
        )
        logger.info(f"SVG → PNG: {len(png_data)} bytes, {int(best_width)}×{int(new_height)}")
    except (ImportError, OSError):
        logger.warning("cairosvg is not installed or missing DLLs. Skipping PNG render.")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)
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
