// Read-only live data with intercepted Worker controls; never starts real work.
const {chromium} = require('C:/Users/zk/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
  const [base, task, output] = process.argv.slice(2);
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, executablePath:'C:/Users/zk/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'});
  try {
    const page = await browser.newPage({viewport:{width:1920,height:1080}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let worker = 'paused', status = 'pending', wakes = 0, submissions = 0, blocked = false;
    await page.route('**/api/recordings/' + task + '/segments', async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.worker = {state:worker};
      data.analysis_job = {job_id:'isolated-browser-job',status,progress:blocked ? {reason:'CUDA unavailable'} : {}};
      await route.fulfill({response,json:data});
    });
    await page.route('**/api/recordings/' + task + '/evidence?*', async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.total = 3;
      data.summary = {completed:0};
      data.candidates = [
        {state:'processing',timings:{judgment:{status:'processing'}}},
        {state:'processing',timings:{auxiliary_asr:{status:'processing'}}},
        {state:'processing',timings:{judgment:{status:'done'}}},
      ];
      await route.fulfill({response,json:data});
    });
    await page.route('**/api/worker/wake', async route => {
      wakes++;
      worker = 'running';
      await route.fulfill({json:{state:'starting'}});
    });
    await page.route('**/api/actions', async route => {
      submissions++;
      await route.fulfill({status:409,json:{detail:'No real tasks allowed in browser acceptance'}});
    });
    const url = base + '/#/library?source_task_id=' + task + '&mode=subtitles';
    await page.goto(url, {waitUntil:'networkidle'});
    await page.locator('#analysis-title').filter({hasText:'AI 分析已暂停'}).waitFor();
    assert.equal(await page.locator('#recording-analysis').getAttribute('aria-busy'), 'false');
    assert(await page.locator('#analysis-resume').isVisible());
    assert(await page.locator('#analysis-resume').isEnabled());
    assert(await page.locator('#review-analyze-now').isEnabled());
    await page.screenshot({path:path.join(output,'paused.png')});
    await page.locator('#analysis-resume').click();
    await page.locator('#analysis-title').filter({hasText:'AI 分析已排队'}).waitFor();
    assert.equal(wakes,1);
    assert.equal(submissions,0);
    worker = 'offline'; status = 'processing';
    await page.locator('#analysis-title').filter({hasText:'后台未运行'}).waitFor({timeout:15000});
    await page.locator('#review-analyze-now').click();
    await page.locator('#analysis-detail').filter({hasText:'候选 1：准备 AI 视频'}).waitFor();
    assert.match(await page.locator('#analysis-detail').innerText(), /候选 3：判断已结束，等待汇总/);
    assert.equal(wakes,2);
    assert.equal(submissions,0);
    await page.screenshot({path:path.join(output,'processing.png')});
    status = 'pending'; blocked = true;
    await page.locator('#analysis-title').filter({hasText:'暂时无法开始'}).waitFor({timeout:15000});
    assert.equal(await page.locator('#recording-analysis').getAttribute('aria-busy'), 'false');
    assert.match(await page.locator('#analysis-detail').innerText(), /CUDA unavailable/);
    assert.deepEqual(errors,[]);
    console.log('BROWSER_PASS paused/offline recovery, no duplicate submission, live stage display, blocked environment; zero page errors; real Worker controls intercepted');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
