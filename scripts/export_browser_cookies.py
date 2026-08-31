#!/usr/bin/env python3
"""Export AutoZone Pro session cookies from a local browser profile (on-device only).

Requires: pip install browser-cookie3

Usage:
  python scripts/export_browser_cookies.py --browser brave
  python scripts/export_browser_cookies.py --browser chrome --out ~/.config/autozonepro_cookies.json

Supported --browser: chrome, chromium, brave, firefox, edge, opera, vivaldi
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DOMAIN_HINTS = ("autozonepro.com", "autozone.com")


def load_jar(browser: str):
    try:
        import browser_cookie3 as bc
    except ImportError:
        print("Install browser-cookie3:  pip install browser-cookie3", file=sys.stderr)
        sys.exit(2)
    loaders = {
        "chrome": bc.chrome,
        "chromium": bc.chromium,
        "brave": bc.brave,
        "firefox": bc.firefox,
        "edge": bc.edge,
        "opera": bc.opera,
        "vivaldi": getattr(bc, "vivaldi", None) or bc.chrome,
    }
    fn = loaders.get(browser.lower())
    if not fn:
        print(f"Unknown browser {browser!r}. Choose: {', '.join(loaders)}", file=sys.stderr)
        sys.exit(2)
    return fn()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--browser",
        required=True,
        help="Browser that is logged into AutoZone Pro (chrome, brave, firefox, edge, …)",
    )
    ap.add_argument(
        "--out",
        default=str(Path.home() / ".config" / "autozonepro_cookies.json"),
        help="Output path (default: ~/.config/autozonepro_cookies.json)",
    )
    args = ap.parse_args()

    jar = load_jar(args.browser)
    out: dict[str, str] = {}
    for c in jar:
        dom = (c.domain or "").lstrip(".").lower()
        if any(h in dom for h in DOMAIN_HINTS):
            out[c.name] = c.value

    if not out:
        print(
            f"No AutoZone cookies found in {args.browser}. "
            "Log into https://www.autozonepro.com in that browser, then re-run.",
            file=sys.stderr,
        )
        return 1

    path = Path(args.out).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(out)} cookies → {path}")
    print("Cookies stayed on this machine. Do not commit this file to git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
