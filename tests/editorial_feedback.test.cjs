const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { TextEncoder } = require('node:util');

function context() {
  const storage = new Map();
  const ctx = vm.createContext({Date, Intl, TextEncoder, btoa,
    localStorage: {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
  });
  for (const path of ['frontend/ui.js', 'frontend/editorial.js', 'frontend/submission.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), ctx);
  }
  return {ctx, storage};
}

test('history renders actual before/after safely and leaves stored order intact', () => {
  const {ctx} = context();
  const events = [{sequence: 1, action: 'range', created_at: '2026-10-18T12:00:00Z',
    before: {source_end: 15.12}, after: {source_end: 18.34}, feedback: {reason: '<img src=x onerror=alert(1)>', uncertain: true}},
  {sequence: 2, action: 'metadata', created_at: '2026-10-18T12:01:00Z',
    before: {title: '原文'}, after: {title: '新文'}, feedback: {reason: ''}}];
  ctx.s = {editorial_history: events};
  const html = vm.runInContext('editorialHistoryHTML(s)', ctx);
  assert.match(html, /修改前/);
  assert.match(html, /00:00:15\.12.*00:00:18\.34/s);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /拿不准/);
  assert.ok(html.indexOf('修改文案') < html.indexOf('调整范围'));
  assert.deepEqual(events.map(e => e.sequence), [1, 2]);
});

test('model notes never prefill human feedback; stale drafts are visible for confirmation', () => {
  const {ctx, storage} = context();
  ctx.s = {segment_id: 'a', revision: 2, note: '模型觉得很好笑'};
  assert.doesNotMatch(vm.runInContext('editorialFeedbackHTML(s)', ctx), /模型觉得很好笑/);
  storage.set('bilive:editorial:a', JSON.stringify({revision: 1, reason: '人工尚未保存', uncertain: true}));
  const html = vm.runInContext('editorialFeedbackHTML(s)', ctx);
  assert.match(html, /人工尚未保存/);
  assert.match(html, /草稿来自旧版本/);
  assert.match(html, /checked/);
});

function submission(ctx) {
  const segment = {segment_id: 'a', source_relpath: '123/session.mp4', revision: 2};
  const controls = ['原标题', '简介', '聊天', '27', '', '', '动态', '原文没有两小时'].map(value => ({value}));
  const nodes = {'submission-reason': controls[7], 'submission-suggestion': {}, 'submission-editorial-history': {}};
  ctx.document = {getElementById: id => nodes[id]};
  ctx.metadataSuggestionHTML = () => '';
  ctx.view = {segment, controls, draftRevision: 2, dirty: true, current: () => true};
  return {controls, nodes};
}

test('submission sends the optional reason and clears it only after successful persistence', async () => {
  const {ctx} = context();
  const {controls, nodes} = submission(ctx);
  let request;
  ctx.reviewAPI = async (path, body) => {
    request = body;
    return {...ctx.view.segment, revision: 3, dynamic: body.dynamic, editorial_history: []};
  };
  await vm.runInContext('saveSubmission(view)', ctx);
  assert.equal(request.reason, '原文没有两小时');
  assert.equal(request.title, '原标题');
  assert.equal(request.dynamic, '动态');
  assert.equal(controls[7].value, '');
  assert.equal(ctx.view.dirty, false);
  assert.match(nodes['submission-editorial-history'].innerHTML, /取舍与修改记录/);
});

test('failed submission preserves the reason, metadata and local draft', async () => {
  const {ctx, storage} = context();
  const {controls} = submission(ctx);
  const key = vm.runInContext('submissionDraftKey(view.segment)', ctx);
  storage.set(key, 'existing draft');
  ctx.reviewAPI = async (path, body) => {
    if (body) throw new Error('revision conflict');
    return {...ctx.view.segment, revision: 3};
  };
  await assert.rejects(vm.runInContext('saveSubmission(view)', ctx), /revision conflict/);
  assert.equal(controls[7].value, '原文没有两小时');
  assert.equal(controls[0].value, '原标题');
  assert.equal(ctx.view.dirty, true);
  assert.equal(storage.get(key), 'existing draft');
});
