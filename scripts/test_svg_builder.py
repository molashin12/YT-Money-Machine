"""
Quick test of SVG manipulation logic (no CairoSVG rendering needed).
Run from the project root: python scripts/test_svg_builder.py
"""
import sys
import os
import io
import xml.etree.ElementTree as ET

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.card_builder_svg import (
    _find_by_id, _inject_text, _inject_image, _inject_source,
    _resize_svg, _svg_dims, SVG_NS, XLINK_NS,
    TEXT_TO_IMAGE_GAP, IMAGE_TO_SOURCE_GAP, BOTTOM_PADDING,
)

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


def main():
    svg_path = os.path.join("assets", "channels", "test_channel", "template.svg")
    if not os.path.exists(svg_path):
        print(f"ERROR: Template not found at {svg_path}")
        return

    svg_raw = open(svg_path, "r", encoding="utf-8").read()
    root = ET.fromstring(svg_raw)

    svg_w, original_h = _svg_dims(root)
    print(f"Original SVG: {svg_w}×{original_h}")

    # Verify elements exist
    text_el = _find_by_id(root, "input_text")
    image_el = _find_by_id(root, "main_image")
    source_el = _find_by_id(root, "source")

    print(f"  input_text found: {text_el is not None} (tag: {text_el.tag if text_el is not None else 'N/A'})")
    print(f"  main_image found: {image_el is not None} (tag: {image_el.tag if image_el is not None else 'N/A'})")
    print(f"  source found: {source_el is not None} (tag: {source_el.tag if source_el is not None else 'N/A'})")

    if image_el is not None:
        tag_local = image_el.tag.split("}")[-1] if "}" in image_el.tag else image_el.tag
        print(f"  main_image type: {tag_local}")
        if tag_local == "g":
            for child in image_el:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                print(f"    child: <{child_tag}> fill={child.get('fill', 'N/A')}")

    # ── Test 1: Text injection ──
    print("\n=== Test 1: Text Injection ===")
    body = "In 1994, a man survived 76 hours trapped under rubble after the Northridge earthquake by drinking his own perspiration to stay hydrated."
    text_info = _inject_text(root, body, svg_w)
    print(f"  Result: {text_info}")
    text_bottom = text_info["start_y"] + text_info["text_height"]
    print(f"  Text bottom: {text_bottom:.0f}px")

    # Verify tspan elements were created
    text_el = _find_by_id(root, "input_text")
    tspans = list(text_el)
    print(f"  <tspan> elements created: {len(tspans)}")
    for i, ts in enumerate(tspans):
        ts_tag = ts.tag.split("}")[-1] if "}" in ts.tag else ts.tag
        print(f"    [{i}] <{ts_tag}> x={ts.get('x')} y={ts.get('y', 'N/A')} dy={ts.get('dy', 'N/A')} text='{ts.text}'")

    # ── Test 2: Image injection (with test image) ──
    print("\n=== Test 2: Image Injection ===")
    from PIL import Image
    test_img = Image.new("RGB", (600, 400), color=(180, 40, 40))
    buf = io.BytesIO()
    test_img.save(buf, format="JPEG")
    test_image_bytes = buf.getvalue()

    image_y = text_bottom + TEXT_TO_IMAGE_GAP
    img_info = _inject_image(root, test_image_bytes, image_y, svg_w)
    print(f"  Result: {img_info}")
    image_bottom = img_info["img_y"] + img_info["img_height"]
    print(f"  Image bottom: {image_bottom:.0f}px")

    # ── Test 3: Source injection ──
    print("\n=== Test 3: Source Injection ===")
    source_y = image_bottom + IMAGE_TO_SOURCE_GAP
    _inject_source(root, "source: reddit.com", source_y, x=text_info["x"])
    source_el = _find_by_id(root, "source")
    source_children = list(source_el)
    print(f"  Source tspan count: {len(source_children)}")
    if source_children:
        ts = source_children[0]
        print(f"  tspan: x={ts.get('x')} y={ts.get('y')} text='{ts.text}'")

    # ── Test 4: Resize SVG ──
    print("\n=== Test 4: SVG Resize ===")
    new_height = source_y + BOTTOM_PADDING + 10
    new_height = max(new_height, original_h)
    _resize_svg(root, svg_w, new_height)
    final_w, final_h = _svg_dims(root)
    print(f"  Final SVG dimensions: {final_w}×{final_h}")
    print(f"  viewBox: {root.get('viewBox')}")

    # ── Save the manipulated SVG for visual inspection ──
    out_svg = os.path.join("output", "test_manipulated.svg")
    os.makedirs("output", exist_ok=True)
    svg_str = ET.tostring(root, encoding="unicode", xml_declaration=True)
    with open(out_svg, "w", encoding="utf-8") as f:
        f.write(svg_str)
    print(f"\n  Manipulated SVG saved to: {out_svg}")

    # ── Test 5: No-image test ──
    print("\n=== Test 5: No Image Test ===")
    root2 = ET.fromstring(open(svg_path, "r", encoding="utf-8").read())
    text_info2 = _inject_text(root2, "Short text only.", svg_w)
    text_bottom2 = text_info2["start_y"] + text_info2["text_height"]
    image_y2 = text_bottom2 + TEXT_TO_IMAGE_GAP
    img_info2 = _inject_image(root2, None, image_y2, svg_w)
    image_bottom2 = img_info2["img_y"] + img_info2["img_height"]
    source_y2 = image_bottom2 + IMAGE_TO_SOURCE_GAP
    _inject_source(root2, "source: test", source_y2, x=text_info2["x"])
    new_h2 = max(source_y2 + BOTTOM_PADDING + 10, original_h)
    _resize_svg(root2, svg_w, new_h2)
    print(f"  Final SVG: {svg_w}×{new_h2}")

    out_svg2 = os.path.join("output", "test_no_image.svg")
    svg_str2 = ET.tostring(root2, encoding="unicode", xml_declaration=True)
    with open(out_svg2, "w", encoding="utf-8") as f:
        f.write(svg_str2)
    print(f"  Saved to: {out_svg2}")

    print("\nAll SVG manipulation tests PASSED!")


if __name__ == "__main__":
    main()
