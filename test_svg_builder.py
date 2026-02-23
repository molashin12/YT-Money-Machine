"""Quick test for SVG card builder."""
import asyncio
from app.services.card_builder_svg import (
    _wrap_text, _find_element_by_id, _inject_text, _inject_image, _inject_source,
    _update_svg_dimensions, _get_svg_dimensions, build_card_svg,
)
from app.config import ChannelConfig
import xml.etree.ElementTree as ET

# Test 1: text wrapping
print("=== Test 1: Text wrapping ===")
text = "In 2005, the Met revealed Iris Apfels secret closet. Her ageless fashion proved a chilling truth more is never enough."
lines = _wrap_text(text, 38)
print(f"Wrapped into {len(lines)} lines:")
for l in lines:
    print(f"  [{len(l):2d}] {l}")
assert len(lines) > 1, "Should wrap into multiple lines"

# Test 2: XML element finding
print("\n=== Test 2: Element finding ===")
tree = ET.parse("test_template.svg")
root = tree.getroot()
for eid in ["input_text", "main_image", "source"]:
    elem = _find_element_by_id(root, eid)
    status = "FOUND" if elem is not None else "MISSING"
    print(f"  id='{eid}': {status}")
    assert elem is not None, f"Element {eid} should be found"

# Test 3: Dimensions
print("\n=== Test 3: SVG dimensions ===")
w, h = _get_svg_dimensions(root)
print(f"  Width={w}, Height={h}")
assert w == 400 and h == 800

# Test 4: Text injection
print("\n=== Test 4: Text injection ===")
num_lines, text_height = _inject_text(root, text, w)
print(f"  Lines={num_lines}, Height={text_height}px")
assert num_lines > 1
assert text_height > 0

# Test 5: Source injection
print("\n=== Test 5: Source injection ===")
_inject_source(root, "source: test.com", 700)
source_elem = _find_element_by_id(root, "source")
assert source_elem.text == "source: test.com"
print("  Source text set correctly")

# Test 6: Height update
print("\n=== Test 6: Height update ===")
_update_svg_dimensions(root, 900, w)
assert root.get("height") == "900"
assert root.get("viewBox") == "0 0 400 900"
print(f"  New height=900, viewBox updated")

print("\n✅ All unit tests passed!")
