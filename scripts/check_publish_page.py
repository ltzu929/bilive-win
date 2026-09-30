from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:2234/", wait_until="commit", timeout=15000)
    print("commit ok", page.url)
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/publish'")
    page.wait_for_timeout(1500)
    html = page.inner_html("#main")
    print("MAIN_LEN", len(html))
    print(html[:400].replace("\n", " "))
    print("ERRORS", errors[:8])
    page.screenshot(path=r"D:\bilive\docs\compose\shots\publish_fixed.png")
    b.close()
