"""
Card Builder — uses Gemini 2.5 Flash (Nano Banana) to edit user-designed
card templates by adding the extracted fact text and a related image.

Workflow:
1. Load the channel's pre-designed template image (assets/channels/<slug>/template.png)
2. Send the template + related image + fact text to Gemini as a multimodal prompt
3. Gemini edits the template to compose the final card with text and image
4. The result is placed on a 1080×1920 transparent canvas for video overlay
"""

import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image
from google.genai import types as genai_types

from app.config import (
    ChannelConfig,
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    CARD_MARGIN,
)
from app.services.api_key_manager import get_key_manager

logger = logging.getLogger(__name__)


CARD_EDIT_PROMPT = """You are editing a social media card template for a YouTube Shorts video.

The FIRST image is the blank card template — it already has the channel's logo, name, and verified badge designed into it. There are empty areas where the fact text and a related image should go.

{image_instruction}

Your task: Edit the template to create the final card by:
1. Add this fact title as a heading text on the card: "{title}"
2. Add this fact body text below the title: "{body}"
3. Place the related image in the image area of the card (the lower portion)
4. Add a small source attribution text in the lower-left corner of the card: "{source}"

Rules:
- Keep the EXACT same visual style, colors, fonts, and layout of the original template
- The text should be clean, readable, and fit naturally within the card
- The related image should fill the image area cleanly with no distortion
- Do NOT change the logo, channel name, verified badge, or card background
- The source attribution should be small, subtle text in the bottom-left corner (like "source: example.com")
- The card should look like a polished social media post (like a tweet or Instagram card)
- Output the final edited card as a single image
"""


async def build_card(
    channel: ChannelConfig,
    title: str,
    body: str,
    related_image: Optional[bytes] = None,
    image_source: str = "",
) -> Optional[bytes]:
    """
    Edit the channel's template image using Gemini 2.5 Flash (Nano Banana)
    to add the fact text, related image, and source attribution.

    Returns PNG bytes of the final card on a 1080×1920 canvas, or None on failure.
    """
    logger.info(f"Building card for channel: {channel.name}")

    # Load template
    if not channel.template_path or not Path(channel.template_path).exists():
        logger.error(
            f"No template found for channel '{channel.slug}'. "
            f"Please add a template image to: assets/channels/{channel.slug}/template.png"
        )
        return None

    template_bytes = Path(channel.template_path).read_bytes()

    # Build the Gemini prompt
    image_instruction = ""
    if related_image:
        image_instruction = "The SECOND image is the related image that should be placed in the image area of the card."
    else:
        image_instruction = "There is no related image provided, so generate a relevant image to place in the image area based on the fact content."

    source_text = image_source if image_source else "source: web"

    prompt = CARD_EDIT_PROMPT.format(
        title=title,
        body=body,
        image_instruction=image_instruction,
        source=source_text,
    )

    # Build multimodal content parts
    content_parts = [
        genai_types.Part.from_bytes(data=template_bytes, mime_type="image/png"),
    ]

    if related_image:
        # Detect mime type
        mime = "image/jpeg"
        if related_image[:8].startswith(b"\x89PNG"):
            mime = "image/png"
        elif related_image[:4].startswith(b"RIFF"):
            mime = "image/webp"
        content_parts.append(
            genai_types.Part.from_bytes(data=related_image, mime_type=mime)
        )

    content_parts.append(prompt)

    # Call Gemini 2.5 Flash (Nano Banana) via key manager
    try:
        manager = get_key_manager()

        response = await manager.gemini_generate(
            model="gemini-2.5-flash",
            contents=content_parts,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Extract the edited image from response
        edited_image_bytes = None
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    edited_image_bytes = part.inline_data.data
                    break

        if not edited_image_bytes:
            logger.error("Gemini did not return an edited image")
            return None

        logger.info("Successfully received edited card from Gemini")

        # Place the edited card on a 1080×1920 transparent canvas
        return _compose_on_canvas(edited_image_bytes)

    except Exception as e:
        logger.exception(f"Gemini card editing failed: {e}")
        return None


def _compose_on_canvas(card_image_bytes: bytes) -> bytes:
    """
    Place the edited card image onto a 1080×1920 transparent canvas,
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
        # Card is wider: fit by width
        new_w = target_w
        new_h = int(new_w / card_ratio)
    else:
        # Card is taller: fit by height
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
