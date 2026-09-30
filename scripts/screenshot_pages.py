from pathlib import Path
import time

from playwright.sync_api import sync_playwright

out = Path(r"D:\bilive\docs\compose\shots")
out.mkdir(parents=True, exist_ok=True)
pages = {
    "publish": "#/publish",
    "live": "#/live",
    "recycle": "#/recycle",
    "library": "#/library",
    "streamer": "#/streamer",
    "settings": "#/settings",
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1460, "height": 1060},
        device_scale_factor=1,
        bypass_csp=True,
    )
    context.route("**/*", lambda route: route.continue_())
    page = context.new_page()
    page.goto("http://127.0.0.1:2234/", wait_until="networkidle")
    # 强制丢弃缓存后的样式
    page.evaluate("() => { for (const s of document.styleSheets) { try { s.ownerNode && s.ownerNode.remove(); } catch(e){} } }")
    for name, route in pages.items():
        page.goto(f"http://127.0.0.1:2234/{route}?v={int(time.time())}", wait_until="networkidle")
        page.wait_for_timeout(200)
        page.screenshot(path=str(out / f"{name}.png"), full_page=False)
        print("shot", name)
    browser.close()
print("done")
