// Companion to the isolated Python acceptance server. No real creator requests.
const {chromium} = require('C:/Users/zk/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const path = require('path');
const assert = require('node:assert/strict');
(async () => {
  const [base, output] = process.argv.slice(2);
  const browser = await chromium.launch({headless:true, executablePath:'C:/Users/zk/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'});
  try {
    const page = await browser.newPage({viewport:{width:1920,height:1080}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const state = async () => (await page.request.get(base + '/qa/publication-state')).json();
    await page.goto(base + '/#/streamer?room=123', {waitUntil:'networkidle'});
    await page.locator('#profile-season-load').click();
    await page.locator('#profile-default-season option').filter({hasText:'测试合集'}).waitFor({state:'attached'});
    await page.locator('#profile-default-season').selectOption({label:'测试合集 / 正片'});
    await page.getByRole('button',{name:'保存档案',exact:true}).click();
    await page.locator('#profile-message').filter({hasText:'已保存。'}).waitFor();
    await page.reload({waitUntil:'networkidle'});
    assert.equal(JSON.parse(await page.locator('#profile-default-season').inputValue()).id,56);
    await page.locator('#profile-default-season').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'streamer-default.png')});
    const create = async start => {
      const original = (await state()).segment;
      const response = await page.request.post(base + '/api/segments/point',{data:{
        source_relpath:original.source_relpath,source_start:start,source_end:start+2,title:'新切片',note:'verify inheritance'}});
      assert.equal(response.status(),200,await response.text());
      return response.json();
    };
    const inherited = await create(2);
    assert.equal(inherited.season.id,56);
    await page.locator('#profile-default-season').selectOption('');
    await page.getByRole('button',{name:'保存档案',exact:true}).click();
    await page.locator('#profile-message').filter({hasText:'已保存。'}).waitFor();
    assert.equal((await create(4)).season,null);
    assert.equal((await (await page.request.get(base + '/api/segments/' + inherited.segment_id)).json()).season.id,56);
    const original = (await state()).segment;
    await page.goto(base + '/#/library?mode=final&segment_id=' + original.segment_id, {waitUntil:'networkidle'});
    await page.locator('#review-topic-keywords').fill('测试');
    await page.locator('#review-topic-search').click();
    await page.locator('#review-meta-topic option').filter({hasText:'测试话题'}).waitFor({state:'attached'});
    await page.locator('#review-meta-topic').selectOption({label:'测试话题 · 活动'});
    await page.locator('#review-season-load').click();
    await page.locator('#review-meta-season option').filter({hasText:'测试合集'}).waitFor({state:'attached'});
    await page.locator('#review-meta-season').selectOption({label:'测试合集 / 正片'});
    await page.reload({waitUntil:'networkidle'});
    assert.equal(JSON.parse(await page.locator('#review-meta-topic').inputValue()).id, 12);
    assert.equal(JSON.parse(await page.locator('#review-meta-season').inputValue()).section_id, 78);
    await page.locator('#review-metadata-save').click();
    await page.locator('#review-metadata-status').filter({hasText:'投稿资料已保存'}).waitFor();
    let saved = await state();
    assert.equal(JSON.parse(saved.queue.topic).id,12);
    assert.equal(JSON.parse(saved.queue.season).id,56);
    await page.locator('#review-meta-season').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,'selection.png')});
    await page.locator('#review-meta-topic').selectOption('');
    await page.locator('#review-meta-season').selectOption('');
    await page.locator('#review-metadata-save').click();
    await page.locator('#review-metadata-status').filter({hasText:'投稿资料已保存'}).waitFor();
    saved = await state();
    assert.equal(saved.queue.season,null);
    await page.request.post(base + '/qa/publication-lock');
    await page.reload({waitUntil:'networkidle'});
    for (const selector of ['#review-meta-topic','#review-meta-season','#review-topic-search','#review-season-load'])
      assert(await page.locator(selector).isDisabled());
    await page.request.post(base + '/qa/publication-published');
    await page.goto(base + '/#/publish', {waitUntil:'networkidle'});
    await page.getByRole('tab',{name:'发布历史',exact:true}).click();
    await page.locator('#upload-season-retry').click();
    await page.getByText('已确认加入合集',{exact:true}).waitFor();
    saved = await state();
    assert.equal(saved.joins.length,1);
    assert.equal(saved.queue.stage,'published');
    await page.screenshot({path:path.join(output,'collection-result.png')});
    assert.deepEqual(errors,[]);
    console.log('BROWSER_PASS streamer default save/reload/inheritance/clear, selection, draft reload, queue save, clear, locks, collection-only retry; no page errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
