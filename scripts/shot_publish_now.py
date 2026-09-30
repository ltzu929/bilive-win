from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1460, "height": 1060})
    page.goto("http://127.0.0.1:2234/", wait_until="load", timeout=20000)
    page.wait_for_timeout(800)
    # 强制到发布队列
    page.evaluate("location.hash = '#/publish'")
    page.wait_for_timeout(1200)
    # 检查关键样式是否生效
    info = page.evaluate(
        """() => {
      const sb = document.querySelector('.sidebar');
      const cs = getComputedStyle(sb);
      const h1 = document.querySelector('.page-title h1');
      return {
        sidebarBg: cs.backgroundColor,
        h1Color: h1 ? getComputedStyle(h1).color : null,
        h1Size: h1 ? getComputedStyle(h1).fontSize : null,
        sheets: document.styleSheets.length
      };
    }"""
    )
    print(info)
    page.screenshot(path=r"D:\bilive\docs\compose\shots\publish_now.png", full_page=False)
    b.close()
print("shot saved")
