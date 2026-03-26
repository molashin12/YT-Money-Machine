from PIL import ImageFont, ImageDraw, Image
font = ImageFont.truetype('assets/fonts/Inter.ttf', 12)
dummy_img = Image.new('RGB', (1, 1))
draw = ImageDraw.Draw(dummy_img)

w1 = draw.textlength('Unresolved Mysteries', font=font)
w2 = draw.textlength('@UnresolvedMysteries_2026', font=font)
print('Name regular width 12px:', w1)
print('Handle regular width 12px:', w2)
