from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1460, "height": 1060})
    page.goto("http://127.0.0.1:2234/", wait_until="load", timeout=20000)
    page.wait_for_timeout(600)
    page.evaluate("location.hash = '#/publish'")
    page.wait_for_timeout(1500)
    # 强制重绘
    page.evaluate(
        """() => {
      const m = document.getElementById('main');
      m.style.display = 'none';
      void m.offsetHeight;
      m.style.display = '';
    }"""
    )
    page.wait_for_timeout(300)
    page.screenshot(
        path=r"D:\bilive\docs\compose\shots\publish_clip.png",
        clip={"x": 188, "y": 0, "width": 1272, "height": 1060},
    )
    # 也截整页
    page.screenshot(path=r"D:\bilive\docs\compose\shots\publish_full.png")
    print("ok")
    b.close()
