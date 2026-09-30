from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1460, "height": 1060})
    page.goto("http://127.0.0.1:2234/", wait_until="load", timeout=20000)
    page.wait_for_timeout(500)
    page.evaluate("location.hash = '#/publish'")
    page.wait_for_timeout(1200)
    info = page.evaluate(
        """() => {
      const mains = document.querySelectorAll('#main, .main');
      const main = document.getElementById('main');
      const first = main.querySelector('*');
      const cs = first ? getComputedStyle(first) : null;
      // 检测元素中心点实际命中谁
      const r = main.getBoundingClientRect();
      const hit = document.elementFromPoint(r.x + r.width/2, r.y + 80);
      return {
        mainCount: mains.length,
        mainStyle: getComputedStyle(main).cssText.slice(0,120),
        firstTag: first ? first.tagName + '.' + first.className : null,
        firstVis: cs ? {display:cs.display, vis:cs.visibility, opacity:cs.opacity, color:cs.color} : null,
        hitTag: hit ? hit.tagName + '.' + hit.className : null,
        bodySnip: document.body.innerHTML.slice(0, 300),
        text: main.innerText.slice(0, 120)
      };
    }"""
    )
    print(info)
    b.close()
