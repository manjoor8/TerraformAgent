"""Capture UI screenshots for visual verification (requires a running server)."""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
OUT = Path(__file__).resolve().parents[2] / "docs" / "screenshots"


def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else ""
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 980})
        page.on("console", lambda msg: errors.append(f"console[{msg.type}] @ {page.url}: {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: errors.append(f"pageerror @ {page.url}: {exc}"))

        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.screenshot(path=str(OUT / "login.png"), full_page=True)

        page.fill("#email", "admin@terraformagent.local")
        page.fill("#password", "ChangeMe123!")
        page.locator("form button.btn-primary").click()
        page.wait_for_url(f"{BASE}/", timeout=20000)
        page.wait_for_selector("text=Infrastructure dashboard", timeout=20000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "dashboard.png"), full_page=True)

        page.goto(f"{BASE}/llm", wait_until="domcontentloaded")
        page.wait_for_timeout(1400)
        page.screenshot(path=str(OUT / "llm-provider.png"), full_page=True)

        if project_id:
            tabs = [
                ("overview", ""),
                ("chat", "/chat"),
                ("documents", "/documents"),
                ("architecture", "/architecture"),
                ("review", "/review"),
                ("terraform", "/terraform"),
                ("validation", "/validation"),
                ("plan", "/plan"),
                ("deployment", "/deployment"),
                ("audit", "/audit"),
                ("settings", "/settings"),
            ]
            for name, suffix in tabs:
                page.goto(f"{BASE}/projects/{project_id}{suffix}", wait_until="domcontentloaded")
                page.wait_for_timeout(1500 if name in ("architecture", "plan") else 700)
                page.screenshot(path=str(OUT / f"project-{name}.png"), full_page=True)

        browser.close()

    print(f"Saved screenshots to {OUT}")
    if errors:
        print("BROWSER ERRORS:")
        for error in errors[:40]:
            print(" -", error)
    else:
        print("No browser console errors.")


if __name__ == "__main__":
    main()
