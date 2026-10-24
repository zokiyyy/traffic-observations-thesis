#!/usr/bin/env python3
"""Make Unity's default web template fill a desktop browser window."""

from pathlib import Path


WEB = Path(__file__).resolve().parents[1]
HTML = WEB / "Builds/TrafficWebTen/index.html"
old = '''        fullscreenContainer.style.width = "960px";
        fullscreenContainer.style.height = "600px";'''
new = '''        // Preserve the dashboard's 1800:1000 layout while using available space.
        function sizeDashboard() {
          const width = Math.max(500, Math.min(window.innerWidth - 32, (window.innerHeight - 54) * 1.8, 1800));
          fullscreenContainer.style.width = width + "px";
          fullscreenContainer.style.height = (width / 1.8) + "px";
        }
        sizeDashboard();
        window.addEventListener("resize", sizeDashboard);'''


def main():
    html = HTML.read_text(encoding="utf-8")
    if old in html:
        html = html.replace(old, new, 1)
    elif new not in html:
        raise ValueError("Unity web template changed; inspect the layout before publishing")
    html = html.replace("Unity Web Player | Traffic Observations 10", "Traffic Observations | Ten recordings")
    HTML.write_text(html, encoding="utf-8")
    print("Responsive desktop layout applied to", HTML)


if __name__ == "__main__":
    main()
