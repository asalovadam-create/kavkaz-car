"""Разбор User-Agent без внешних библиотек: тип устройства, ОС, браузер, марка телефона.

Важно: по User-Agent НЕЛЬЗЯ узнать точную модель iPhone — Apple её не передаёт. Поэтому модель iPhone
определяется приблизительно по размеру экрана (его присылает браузер, см. analytics.py) и показывается
группой («iPhone 14 Pro / 15 / 15 Pro / 16»). У Android модель обычно есть в User-Agent (код вроде SM-S918B),
но свежие версии Chrome её скрывают — тогда остаётся только «Android».
"""
import re

BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|curl|wget|python|headless|monitor|uptime|lighthouse|"
    r"facebookexternalhit|gtmetrix|pingdom|scanner|httpclient|go-http|java/|okhttp/|axios",
    re.I,
)

# (ширина, высота, масштаб) в «CSS-пикселях» в портретной ориентации -> группа моделей
IPHONE_SCREENS = {
    (320, 568, 2): "iPhone 5 / 5s / SE (1-го пок.)",
    (375, 667, 2): "iPhone 6 / 6s / 7 / 8 / SE (2-го и 3-го пок.)",
    (414, 736, 3): "iPhone 6 Plus / 7 Plus / 8 Plus",
    (375, 812, 3): "iPhone X / XS / 11 Pro / 12 mini / 13 mini",
    (414, 896, 2): "iPhone XR / 11",
    (414, 896, 3): "iPhone XS Max / 11 Pro Max",
    (390, 844, 3): "iPhone 12 / 12 Pro / 13 / 13 Pro / 14",
    (428, 926, 3): "iPhone 12 Pro Max / 13 Pro Max / 14 Plus",
    (393, 852, 3): "iPhone 14 Pro / 15 / 15 Pro / 16",
    (430, 932, 3): "iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus",
    (402, 874, 3): "iPhone 16 Pro / 17 / 17 Pro",
    (440, 956, 3): "iPhone 16 Pro Max / 17 Pro Max",
    (420, 912, 3): "iPhone Air",
}

_ANDROID_BRANDS = [
    (re.compile(r"^(SM-|GT-|SAMSUNG)", re.I), "Samsung"),
    (re.compile(r"^Pixel", re.I), "Google"),
    (re.compile(r"(redmi|poco|xiaomi|^mi\s)", re.I), "Xiaomi"),
    (re.compile(r"(huawei|^honor)", re.I), "Huawei/Honor"),
    (re.compile(r"^CPH\d", re.I), "OPPO"),
    (re.compile(r"^RMX\d", re.I), "realme"),
    (re.compile(r"^V\d{4}", re.I), "vivo"),
    (re.compile(r"(oneplus|^(IN|KB|LE|HD|NE|GM)\d{4}$)", re.I), "OnePlus"),
    (re.compile(r"(^moto|^XT\d{4})", re.I), "Motorola"),
    (re.compile(r"^(tecno|infinix|itel)", re.I), ""),
]

_WINDOWS = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7"}


def is_bot(ua: str) -> bool:
    return not ua or bool(BOT_RE.search(ua))


def iphone_guess(width: int, height: int, dpr: float) -> str | None:
    return IPHONE_SCREENS.get((width, height, round(dpr)))


def _android_model(raw: str) -> tuple[str, str]:
    for pattern, brand in _ANDROID_BRANDS:
        if pattern.search(raw):
            return brand, raw
    return "", raw


def parse_ua(ua: str) -> dict:
    info = {"device_type": "desktop", "brand": "", "model": "", "os_name": "", "os_version": "", "browser": ""}

    if "iPhone" in ua:
        info.update(device_type="mobile", brand="Apple", model="iPhone", os_name="iOS")
        m = re.search(r"OS (\d+)[_.](\d+)", ua)
        if m:
            info["os_version"] = f"{m.group(1)}.{m.group(2)}"
    elif "iPad" in ua:
        info.update(device_type="tablet", brand="Apple", model="iPad", os_name="iPadOS")
        m = re.search(r"OS (\d+)[_.](\d+)", ua)
        if m:
            info["os_version"] = f"{m.group(1)}.{m.group(2)}"
    elif "Android" in ua:
        info["os_name"] = "Android"
        info["device_type"] = "mobile" if "Mobile" in ua else "tablet"
        m = re.search(r"Android ([\d.]+)", ua)
        if m:
            info["os_version"] = m.group(1)
        mm = re.search(r"Android [\d.]+;\s*([^;)]+?)(?:\s+Build|[;)])", ua)
        if mm:
            raw = mm.group(1).strip()
            if len(raw) > 1 and raw not in ("K", "U", "Linux", "wv"):
                info["brand"], info["model"] = _android_model(raw)
    elif "Windows" in ua:
        info["os_name"] = "Windows"
        m = re.search(r"Windows NT ([\d.]+)", ua)
        if m:
            info["os_version"] = _WINDOWS.get(m.group(1), m.group(1))
    elif "CrOS" in ua:
        info["os_name"] = "ChromeOS"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        info.update(brand="Apple", model="Mac", os_name="macOS")
        m = re.search(r"Mac OS X (\d+)[_.](\d+)", ua)
        if m:
            info["os_version"] = f"{m.group(1)}.{m.group(2)}"
    elif "Linux" in ua:
        info["os_name"] = "Linux"

    if re.search(r"EdgA?/|EdgiOS/|Edge/", ua):
        info["browser"] = "Edge"
    elif re.search(r"OPR/|Opera|OPiOS/", ua):
        info["browser"] = "Opera"
    elif "YaBrowser" in ua or "YaApp_iOS" in ua or "YaSearchBrowser" in ua:
        info["browser"] = "Яндекс Браузер"
    elif "SamsungBrowser" in ua:
        info["browser"] = "Samsung Internet"
    elif "Instagram" in ua:
        info["browser"] = "Instagram (внутри приложения)"
    elif "FBAN" in ua or "FBAV" in ua:
        info["browser"] = "Facebook (внутри приложения)"
    elif "Firefox/" in ua or "FxiOS/" in ua:
        info["browser"] = "Firefox"
    elif "CriOS/" in ua or "Chrome/" in ua:
        info["browser"] = "Chrome"
    elif "Safari/" in ua:
        info["browser"] = "Safari"
    return info
