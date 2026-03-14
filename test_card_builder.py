import asyncio
import os
from pathlib import Path
from app.services.card_builder_svg import build_card_svg
from app.config import ChannelConfig

async def main():
    channel = ChannelConfig(
        name="test_channel",
        slug="test_channel"
    )
    
    img_path = "assets/channels/test_channel/D19552AD-2D75-4FCC-9C06-8B377A9EA3AC_1_105_c.jpg"
    img = None
    if os.path.exists(img_path):
        with open(img_path, "rb") as f:
            img = f.read()
            
    body = "This is a very long text that should be wrapped properly and if it's too long, the svg should resize by height and the background should be updated. We will need to see if the background covers the new height properly and the image is moved down accordingly." * 5
    
    out = await build_card_svg(channel, "Test Title", body, img, "Test Source")
    
    if out:
        with open("test_output.png", "wb") as f:
            f.write(out)
        print("Success, wrote test_output.png")
    else:
        print("Failed")

if __name__ == "__main__":
    asyncio.run(main())
