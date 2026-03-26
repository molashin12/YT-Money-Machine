from PIL import ImageFont, ImageDraw, Image
font = ImageFont.truetype('assets/fonts/Inter.ttf', 12)
dummy_img = Image.new('RGB', (1, 1))
draw = ImageDraw.Draw(dummy_img)
bbox = draw.textbbox((0, 0), 'Unresolved Mysteries', font=font)
print('Width Regular:', bbox[2] - bbox[0])
width = draw.textlength('Unresolved Mysteries', font=font)
print('length Regular:', width)

font_bold = ImageFont.truetype('assets/fonts/Inter.ttf', 12)
# can't fake bold in PIL without a real font
