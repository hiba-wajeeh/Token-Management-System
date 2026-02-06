import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import sys
import subprocess

def _load_font(path: str, size: int):
    # fallback to default PIL font if Arial isn't available on some machines
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def _generate_token_image(token_no: int, dept: str, width: int, height: int):
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Scale fonts relative to width (so higher printer DPI => sharper text)
    # Base was 384px wide
    scale = width / 384.0

    font_big   = _load_font("arialbd.ttf", int(80 * scale))
    font_mid   = _load_font("arialbd.ttf", int(36 * scale))

    # ✅ Make date/time as clear as possible: bigger + bold + pure black
    font_time  = _load_font("arialbd.ttf", int(30 * scale))

    y = int(20 * scale)

    # ---- LOGO ----
    if os.path.exists("logo.png"):
        logo = Image.open("logo.png").convert("RGBA")
        logo_size = int(160 * scale)
        logo = logo.resize((logo_size, logo_size))
        img.paste(logo, ((width - logo_size)//2, y), logo)
        y += int(180 * scale)

    # ---- TOKEN LABEL ----
    draw.text(
        (width // 2, y),
        "TOKEN NO",
        fill="black",
        font=font_mid,
        anchor="mm"
    )
    y += int(50 * scale)

    # ---- TOKEN NUMBER ----
    draw.text(
        (width // 2, y),
        str(token_no),
        fill="black",
        font=font_big,
        anchor="mm"
    )
    y += int(110 * scale)

    # ---- DATE & TIME ----
    now = datetime.now().strftime("%d %b %Y  |  %I:%M %p")

    # Optional: add a tiny "stroke" to make it extra crisp on thermal printers
    draw.text(
        (width // 2, y),
        now,
        fill="black",
        font=font_time,
        anchor="mm",
        stroke_width=max(1, int(1 * scale)),
        stroke_fill="black"
    )

    return img


def print_token(printer_name: str, token_no: int, dept: str):
    # ---------- WINDOWS: silent direct print ----------
    if sys.platform.startswith("win"):
        try:
            import win32print
            import win32ui
            from PIL import ImageWin

            if not printer_name.strip():
                printer_name = win32print.GetDefaultPrinter()

            print(f"[PRINT] Using printer: {printer_name}")

            hDC = win32ui.CreateDC()
            hDC.CreatePrinterDC(printer_name)

            printable_w = hDC.GetDeviceCaps(8)   # HORZRES
            printable_h = hDC.GetDeviceCaps(10)  # VERTRES

            target_w = max(384, printable_w)
            target_h = int(target_w * (500 / 384))

            if target_h > printable_h and printable_h > 0:
                target_h = printable_h

            img = _generate_token_image(token_no, dept, target_w, target_h)

            hDC.StartDoc("PAD Token")
            hDC.StartPage()

            dib = ImageWin.Dib(img)
            dib.draw(hDC.GetHandleOutput(), (0, 0, target_w, target_h))

            hDC.EndPage()
            hDC.EndDoc()
            hDC.DeleteDC()
            return

        except Exception as e:
            print("PRINT ERROR (Windows):", e)

    # ---------- macOS: save + open print dialog ----------
    try:
        # Use temp folder so project stays clean
        out_dir = "/tmp"
        os.makedirs(out_dir, exist_ok=True)

        out_path = os.path.join(
            out_dir,
            f"token_{token_no}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )

        # Use your original 384x500 design (perfect for thermal)
        img = _generate_token_image(token_no, dept, 384, 500)
        img.save(out_path, "PNG")

        print(f"[PRINT] macOS print file generated: {out_path}")

        # Open print dialog (user selects printer)
        subprocess.run(["open", out_path], check=False)

    except Exception as e:
        print("PRINT ERROR (macOS):", e)
        try:
            img.save("print_failed.png")
        except Exception:
            pass
