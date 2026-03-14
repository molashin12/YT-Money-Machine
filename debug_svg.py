import asyncio
import os
from pathlib import Path
from app.config import ChannelConfig
from app.services.card_builder_svg import build_card_svg

async def main():
    channel = ChannelConfig(
        name="test_channel",
        slug="test_channel"
    )
    # Using real channel directory to use their actual SVG template
    channel.svg_template_path = "assets/channels/test_channel/template.svg"
    channel.logo_path = "assets/channels/test_channel/D19552AD-2D75-4FCC-9C06-8B377A9EA3AC_1_105_c.jpg"

    img_path = channel.logo_path
    img = None
    if os.path.exists(img_path):
        with open(img_path, "rb") as f:
            img = f.read()

    body = "This is a very long text that should be wrapped properly." * 5
    
    out = await build_card_svg(channel, "Test Title", body, img, "Test Source")
    if out:
        with open("test_output_debug.svg", "wb") as f:
            f.write(out)
        print("Wrote test_output_debug.svg!!!")
    else:
        print("Failed to build!")

if __name__ == "__main__":
    asyncio.run(main())
