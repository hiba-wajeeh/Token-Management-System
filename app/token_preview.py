from PIL import Image, ImageDraw, ImageFont
import qrcode
import os

WIDTH = 384   # 58mm printer (use 576 for 80mm)
BG = "white"
FG = "black"

def generate_token_preview(token_no: int, qr_text: str):
    height = 700
    img = Image.new("RGB", (WIDTH, height), BG)
    draw = ImageDraw.Draw(img)

    # Fonts (Windows safe)
    font_big = ImageFont.truetype("arialbd.ttf", 72)
    font_mid = ImageFont.truetype("arialbd.ttf", 36)
    font_small = ImageFont.truetype("arial.ttf", 22)

    y = 20

    # ---- LOGO ----
    if os.path.exists("logo.png"):
        logo = Image.open("logo.png").convert("RGB")
        logo = logo.resize((180, 180))
        img.paste(logo, ((WIDTH - 180)//2, y))
        y += 200

    # ---- TOKEN LABEL ----
    draw.text((WIDTH//2, y), "TOKEN NO", fill=FG, font=font_mid, anchor="mm")
    y += 50

    # ---- TOKEN NUMBER ----
    draw.text((WIDTH//2, y), str(token_no), fill=FG, font=font_big, anchor="mm")
    y += 110

    # ---- QR CODE ----
    qr = qrcode.make(qr_text)
    qr = qr.resize((220, 220))
    img.paste(qr, ((WIDTH - 220)//2, y))
    y += 240

    # ---- FOOTER ----
    draw.text(
        (WIDTH//2, y),
        "Scan QR code for your feedback",
        fill=FG,
        font=font_small,
        anchor="mm"
    )

    img.save("token_preview.png")
    img.show()   # opens preview window

if __name__ == "__main__":
    generate_token_preview(
        token_no=4007,
        qr_text="https://pad.org/feedback?welfare"
    )
