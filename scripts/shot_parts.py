from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1460, "height": 1060})
    page.goto("http://127.0.0.1:2234/", wait_until="load", timeout=20000)
    page.wait_for_timeout(500)
    page.evaluate("location.hash = '#/publish'")
    page.wait_for_timeout(1200)
    page.locator(".page-title").screenshot(path=r"D:\bilive\docs\compose\shots\title_only.png")
    page.locator("#main").screenshot(path=r"D:\bilive\docs\compose\shots\main_only.png")
    # 去掉水印后再截整页
    page.evaluate("document.querySelectorAll('[data-aigc-mark]').forEach(e => e.remove())")
    page.wait_for_timeout(200)
    page.screenshot(path=r"D:\bilive\docs\compose\shots\publish_clean.png")
    print("done")
    b.close()
