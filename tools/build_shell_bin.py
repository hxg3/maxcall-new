#!/usr/bin/env python3
"""Build res/main_shell.bin from web-shell/ sources.

Reads web-shell/index.html, inlines web-shell/app.js in place of
<script src="./app.js"></script>, inlines the vendored Tailwind Play engine
in place of the cdn.tailwindcss.com tag, and writes the result as UTF-8
(without BOM) to res/main_shell.bin, which is embedded into
MaxCall.exe as IDR_MAIN_SHELL_HTML (see res/embedded.rc2).

The Tailwind engine is vendored rather than fetched. It used to load from
https://cdn.tailwindcss.com, which is a render-blocking tag in <head>: with no
route to that CDN the page never paints and the app opens on a blank window.
Shipping it inside the exe makes both the sign-in page and the shell render
with no network at all. The vendored file is the unmodified Tailwind Play CDN
v3 build (MIT).

Source of truth is always web-shell/ — never edit the .bin by hand.
Usage: python3 tools/build_shell_bin.py   (run from repo root)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "web-shell" / "index.html"
JS = ROOT / "web-shell" / "app.js"
OUT = ROOT / "res" / "main_shell.bin"
LOGIN_HTML = ROOT / "web-shell" / "login.html"
LOGIN_OUT = ROOT / "res" / "login.bin"
TAILWIND = ROOT / "web-shell" / "tailwind.play.js"
# The caller popup is kept at the repository root and was being copied to
# res/crm_popup.bin by hand, so it silently kept its cdn.tailwindcss.com tag.
CRM_POPUP_HTML = ROOT / "crm_popup.html"
CRM_POPUP_OUT = ROOT / "res" / "crm_popup.bin"
MARKER = '<script src="./app.js"></script>'
TAILWIND_TAG = '<script src="https://cdn.tailwindcss.com"></script>'


def inline_tailwind(html: str, label: str) -> str:
    """Replace the CDN tag with the vendored engine.

    Kept as a runtime script rather than prebuilt CSS so the utility classes
    and the tailwind.config colour overrides keep working exactly as before.
    """
    if TAILWIND_TAG not in html:
        print(f"ERROR: tailwind tag not found in {label}")
        return html
    engine = TAILWIND.read_text(encoding="utf-8")
    if "</script>" in engine:
        print(f"ERROR: vendored tailwind contains a script terminator")
        return html
    return html.replace(TAILWIND_TAG, "<script>\n" + engine + "\n</script>", 1)


def main() -> int:
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    if MARKER not in html:
        print(f"ERROR: marker not found in {HTML}: {MARKER}")
        return 1
    bundled = html.replace(MARKER, "<script>\n" + js + "\n</script>", 1)
    bundled = inline_tailwind(bundled, HTML)
    if TAILWIND_TAG in bundled:
        return 1
    # Auto-notify C++ when the shell finishes loading (snapshot trigger).
    notify = '<script>window.addEventListener("DOMContentLoaded",function(){if(window.MaxCallBridge&&window.MaxCallBridge.ready)window.MaxCallBridge.ready();});</script>'
    bundled = bundled.replace("</body>", notify + "</body>", 1)
    OUT.write_bytes(bundled.encode("utf-8"))
    print(f"OK: wrote {OUT} ({OUT.stat().st_size} bytes)")
    # Caller popup: same offline Tailwind, and generated from the source instead
    # of being a hand-edited binary.
    popup = inline_tailwind(CRM_POPUP_HTML.read_text(encoding="utf-8"), CRM_POPUP_HTML)
    if TAILWIND_TAG in popup:
        return 1
    CRM_POPUP_OUT.write_bytes(popup.encode("utf-8"))
    print(f"OK: wrote {CRM_POPUP_OUT} ({CRM_POPUP_OUT.stat().st_size} bytes)")

    # Login page is standalone, but it needs the same offline Tailwind.
    login = inline_tailwind(LOGIN_HTML.read_text(encoding="utf-8"), LOGIN_HTML)
    if TAILWIND_TAG in login:
        return 1
    LOGIN_OUT.write_bytes(login.encode("utf-8"))
    print(f"OK: wrote {LOGIN_OUT} ({LOGIN_OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
