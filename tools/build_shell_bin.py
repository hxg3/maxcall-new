#!/usr/bin/env python3
"""Build res/main_shell.bin from web-shell/ sources.

Reads web-shell/index.html, inlines web-shell/app.js in place of
<script src="./app.js"></script>, and writes the result as UTF-8
(without BOM) to res/main_shell.bin, which is embedded into
MaxCall.exe as IDR_MAIN_SHELL_HTML (see res/embedded.rc2).

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
MARKER = '<script src="./app.js"></script>'


def main() -> int:
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    if MARKER not in html:
        print(f"ERROR: marker not found in {HTML}: {MARKER}")
        return 1
    bundled = html.replace(MARKER, "<script>\n" + js + "\n</script>", 1)
    # Auto-notify C++ when the shell finishes loading (snapshot trigger).
    notify = '<script>window.addEventListener("DOMContentLoaded",function(){if(window.MaxCallBridge&&window.MaxCallBridge.ready)window.MaxCallBridge.ready();});</script>'
    bundled = bundled.replace("</body>", notify + "</body>", 1)
    OUT.write_bytes(bundled.encode("utf-8"))
    print(f"OK: wrote {OUT} ({OUT.stat().st_size} bytes)")
    # Login page is standalone — embed as-is.
    login = LOGIN_HTML.read_text(encoding="utf-8")
    LOGIN_OUT.write_bytes(login.encode("utf-8"))
    print(f"OK: wrote {LOGIN_OUT} ({LOGIN_OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
