from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1460, "height": 1060})
    page.goto("http://127.0.0.1:2234/", wait_until="commit", timeout=15000)
    page.wait_for_timeout(400)
    page.evaluate("location.hash = '#/publish'")
    page.wait_for_timeout(1500)
    info = page.evaluate(
        """() => {
      const main = document.getElementById('main');
      const head = main.querySelector('.page-head');
      const cs = getComputedStyle(main);
      const csApp = getComputedStyle(document.querySelector('.app'));
      const rMain = main.getBoundingClientRect();
      const rHead = head ? head.getBoundingClientRect() : null;
      return {
        mainRect: {x:rMain.x, y:rMain.y, w:rMain.width, h:rMain.height},
        headRect: rHead ? {x:rHead.x, y:rHead.y, w:rHead.width, h:rHead.height} : null,
        mainDisplay: cs.display, mainOverflow: cs.overflow, mainPad: cs.padding,
        appDisplay: csApp.display, appCols: csApp.gridTemplateColumns,
        bodyW: document.body.getBoundingClientRect().width,
        htmlLen: main.innerHTML.length,
        textSnip: head ? head.innerText : null
      };
    }"""
    )
    print(info)
    page.screenshot(path=r"D:\bilive\docs\compose\shots\publish_dbg.png")
    b.close()
