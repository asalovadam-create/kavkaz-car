"""
Делает все иконки приложения и логотип сайта из ОДНОГО файла.

Использование:
    python tools/make_icons.py путь/к/logotip.png        # из вашего логотипа
    python tools/make_icons.py                           # временная иконка (оранжевый квадрат с «L»)

Создаёт в static/icons: icon-192.png, icon-512.png, icon-maskable-512.png, apple-touch-icon.png,
favicon-32.png и static/img/logo.png (логотип для шапки сайта и админки).
Лучше всего подходит квадратный PNG от 512x512 пикселей.
"""
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(ROOT, "static", "icons")
IMG = os.path.join(ROOT, "static", "img")
BG = (11, 12, 14)            # фон сайта
ACCENT = (201, 138, 62)      # медный цвет бренда


def placeholder(size: int = 1024) -> Image.Image:
    """Временная иконка: медный скруглённый квадрат с буквой L (как логотип в шапке)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.25), fill=ACCENT)
    w = int(size * 0.095)
    x, top, bottom, right = int(size * 0.34), int(size * 0.25), int(size * 0.73), int(size * 0.70)
    d.line([(x, top), (x, bottom), (right, bottom)], fill=(28, 19, 5), width=w, joint="curve")
    for cx, cy in ((x, top), (right, bottom)):
        d.ellipse((cx - w // 2, cy - w // 2, cx + w // 2, cy + w // 2), fill=(28, 19, 5))
    return img


def square(img: Image.Image, size: int, padding: float = 0.0, background=None) -> Image.Image:
    """Вписывает картинку в квадрат size×size с отступом padding (доля от стороны)."""
    inner = int(size * (1 - 2 * padding))
    fitted = img.copy()
    fitted.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2), fitted)
    return canvas


def main() -> None:
    os.makedirs(ICONS, exist_ok=True)
    os.makedirs(IMG, exist_ok=True)
    source = Image.open(sys.argv[1]).convert("RGBA") if len(sys.argv) > 1 else placeholder()
    if len(sys.argv) > 1:
        bbox = source.getbbox()          # обрезаем пустые поля вокруг логотипа
        if bbox:
            source = source.crop(bbox)

    # Обычные иконки: логотип на прозрачном фоне или на фоне сайта (для iOS нужен непрозрачный фон).
    square(source, 192, 0.06).save(os.path.join(ICONS, "icon-192.png"))
    square(source, 512, 0.06).save(os.path.join(ICONS, "icon-512.png"))
    # «Maskable»: Android обрезает иконку по кругу/«капле», поэтому логотип держим в центральных 60%.
    square(source, 512, 0.20, background=BG + (255,)).save(os.path.join(ICONS, "icon-maskable-512.png"))
    # iOS: непрозрачный фон, без прозрачности (иначе фон станет чёрным по-своему).
    apple = square(source, 180, 0.12, background=BG + (255,)).convert("RGB")
    apple.save(os.path.join(ICONS, "apple-touch-icon.png"))
    square(source, 32, 0.0).save(os.path.join(ICONS, "favicon-32.png"))
    square(source, 256, 0.0).save(os.path.join(IMG, "logo.png"))
    print("Готово: иконки в static/icons, логотип в static/img/logo.png")


if __name__ == "__main__":
    main()
