const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function editor({ textDirty = true, styleDirty = true } = {}) {
  const storage = new Map();
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', textContent: '', disabled: false,
      setAttribute() {}, click() { this.clicked = true; }});
    return nodes.get(id);
  };
  node('review-style').value = JSON.stringify({font_size: 48});
  const row = {querySelector: selector => ({value: {
    '[data-start]': '00:00', '[data-end]': '00:02.58', '[data-text]': '人工校对后的字幕',
  }[selector]})};
  const segment = {segment_id: 'clip', revision: 5, subtitles_revision: 5,
    subtitles: [{start: 0, end: 2.58, text: '原字幕'}],
    source_start: 729.77, source_end: 815.74, state: 'needs_finalize'};
  const ctx = vm.createContext({Date, segment,
    localStorage: {getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    document: {getElementById: node,
      querySelector: selector => selector === '#review-lines tr' ? row : null,
      querySelectorAll: selector => selector === '#review-lines tr' ? [row] : []},
  });
  for (const path of ['frontend/ui.js', 'frontend/workbench.js']) {
    vm.runInContext(fs.readFileSync(path, 'utf8'), ctx);
  }
  ctx.textDirty = textDirty;
  ctx.styleDirty = styleDirty;
  vm.runInContext("Object.assign(review, {task: {task_id: 'recording'}, segment, tab: 'subtitles', unsaved: textDirty, styleUnsaved: styleDirty})", ctx);
  const textKey = vm.runInContext('draftKey()', ctx);
  const styleKey = vm.runInContext('styleDraftKey()', ctx);
  if (textDirty) storage.set(textKey, 'text draft');
  if (styleDirty) storage.set(styleKey, 'style draft');
  const requests = [];
  ctx.reviewAPI = async (path, body) => {
    requests.push({path, body});
    return {...segment, revision: body.expected_revision + 1,
      subtitles_revision: body.expected_revision + 1};
  };
  return {ctx, segment, node, storage, textKey, styleKey, requests};
}

test('saved subtitles with a pending style name the remaining draft and offer its save action', () => {
  const {ctx, node} = editor({textDirty: false});
  vm.runInContext('updateReviewActions()', ctx);
  assert.match(node('review-draft-badge').textContent, /样式未保存/);
  assert.match(node('context-save').textContent, /字幕已保存.*样式/);
  assert.equal(node('review-save').textContent, '保存字幕样式');
  assert.equal(node('review-next-step').textContent, '保存字幕样式');
  node('review-next-step').onclick();
  assert.equal(node('review-save').clicked, true);
});

test('the main save persists subtitle text and style with consecutive server revisions', async () => {
  const {ctx, segment, requests, storage, textKey, styleKey} = editor();
  await vm.runInContext('saveReviewSubtitles(segment, reviewAPI)', ctx);
  assert.deepEqual(requests.map(r => [r.path, r.body.expected_revision]), [
    ['/api/subtitles/save', 5], ['/api/segments/clip/style', 6],
  ]);
  assert.equal(requests[0].body.lines[0].text, '人工校对后的字幕');
  assert.equal(requests[1].body.style.font_size, 48);
  assert.equal(segment.revision, 7);
  assert.equal(vm.runInContext('review.unsaved || review.styleUnsaved', ctx), false);
  assert.equal(storage.has(textKey), false);
  assert.equal(storage.has(styleKey), false);
});

test('saving only a style leaves already saved subtitle text alone', async () => {
  const {ctx, requests} = editor({textDirty: false});
  await vm.runInContext('saveReviewSubtitles(segment, reviewAPI)', ctx);
  assert.deepEqual(requests.map(r => [r.path, r.body.expected_revision]), [
    ['/api/segments/clip/style', 5],
  ]);
});

test('a subtitle save failure retains both drafts and does not try to save the style', async () => {
  const {ctx, segment, storage, textKey, styleKey} = editor();
  ctx.reviewAPI = async () => { throw new Error('revision conflict'); };
  await assert.rejects(vm.runInContext('saveReviewSubtitles(segment, reviewAPI)', ctx), /revision conflict/);
  assert.equal(segment.revision, 5);
  assert.equal(vm.runInContext('review.unsaved && review.styleUnsaved', ctx), true);
  assert.equal(storage.get(textKey), 'text draft');
  assert.equal(storage.get(styleKey), 'style draft');
});

test('a failed style save can retry using the saved subtitle revision without rewriting text', async () => {
  const {ctx, segment, requests, storage, textKey, styleKey, node} = editor();
  const persist = ctx.reviewAPI;
  ctx.reviewAPI = async (path, body) => {
    if (path.endsWith('/style')) throw new Error('style unavailable');
    return persist(path, body);
  };
  await assert.rejects(vm.runInContext('saveReviewSubtitles(segment, reviewAPI)', ctx), /style unavailable/);
  assert.equal(segment.revision, 6);
  assert.equal(vm.runInContext('review.unsaved', ctx), false);
  assert.equal(vm.runInContext('review.styleUnsaved', ctx), true);
  assert.equal(storage.has(textKey), false);
  assert.equal(storage.get(styleKey), 'style draft');
  vm.runInContext('updateReviewActions()', ctx);
  assert.match(node('context-save').textContent, /字幕已保存.*样式/);
  ctx.reviewAPI = persist;
  await vm.runInContext('saveReviewSubtitles(segment, reviewAPI)', ctx);
  assert.deepEqual(requests.map(r => [r.path, r.body.expected_revision]), [
    ['/api/subtitles/save', 5], ['/api/segments/clip/style', 6],
  ]);
  assert.equal(segment.revision, 7);
  assert.equal(storage.has(styleKey), false);
});

test('invalid style JSON fails before saving text so both drafts remain available', async () => {
  const {ctx, node, requests, storage, textKey, styleKey} = editor();
  node('review-style').value = '{';
  await assert.rejects(vm.runInContext('saveReviewSubtitles(segment, reviewAPI)', ctx));
  assert.equal(requests.length, 0);
  assert.equal(storage.get(textKey), 'text draft');
  assert.equal(storage.get(styleKey), 'style draft');
});

test('a clean saved subtitle editor disables repeat saves', () => {
  const {ctx, node} = editor({textDirty: false, styleDirty: false});
  vm.runInContext('updateReviewActions()', ctx);
  assert.equal(node('review-save').disabled, true);
  assert.equal(node('review-draft-badge').textContent, '已保存');
});
