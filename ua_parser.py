"""User-Agent parsing utility for MITS ONE.
Detects browser name and device type from HTTP User-Agent header.
Lightweight, fast, and regex-based with zero external dependencies.
"""
import re


def parse_user_agent(ua_string: str) -> dict:
    """Parse User-Agent string into clean browser and device descriptions.

    Returns:
        dict: {'browser': str, 'device': str}
    """
    if not ua_string:
        return {"browser": "Unknown", "device": "Unknown"}

    ua = ua_string.strip()

    # ── Device / OS Detection ───────────────────────────────────────────────
    device = "Desktop"
    ua_lower = ua.lower()

    if "ipad" in ua_lower:
        device = "iPad (Tablet)"
    elif "iphone" in ua_lower:
        device = "iPhone (Mobile)"
    elif "android" in ua_lower:
        if "mobile" in ua_lower:
            device = "Android (Mobile)"
        else:
            device = "Android (Tablet)"
    elif "mobile" in ua_lower:
        device = "Mobile"
    elif "macintosh" in ua_lower or "mac os x" in ua_lower:
        device = "macOS (Desktop)"
    elif "windows nt" in ua_lower:
        device = "Windows (Desktop)"
    elif "linux" in ua_lower:
        device = "Linux (Desktop)"

    # ── Browser Detection ───────────────────────────────────────────────────
    # Order matters because many browsers include 'Safari' or 'Chrome' in UA string
    browser = "Unknown Browser"

    if "edg/" in ua_lower or "edge/" in ua_lower:
        browser = "Microsoft Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        browser = "Opera"
    elif "samsungbrowser" in ua_lower:
        browser = "Samsung Internet"
    elif "brave" in ua_lower:
        browser = "Brave"
    elif "dalvik" in ua_lower:
        browser = "GEMS Mobile App"
    elif "chrome/" in ua_lower or "crios/" in ua_lower:
        browser = "Google Chrome"
    elif "firefox/" in ua_lower or "fxios/" in ua_lower:
        browser = "Mozilla Firefox"
    elif "safari/" in ua_lower and "chrome/" not in ua_lower:
        browser = "Apple Safari"
    elif "curl" in ua_lower:
        browser = "cURL Client"
    elif "python-requests" in ua_lower:
        browser = "Python Requests"
    else:
        # Fallback to first recognizable token
        first_part = ua.split(" ")[0].split("/")[0]
        if first_part and len(first_part) < 30:
            browser = first_part

    return {"browser": browser, "device": device}
