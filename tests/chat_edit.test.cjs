const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const page = vm.createContext({});
for (const file of ['frontend/ui.js','frontend/chat-edit.js']) {
  vm.runInContext(fs.readFileSync(file, 'utf8'), page);
}
const cuts = [[0, 1], [4, 7], [9, 10]];

test('subtitle seeks and cover picks use the same retained frames across a splice', () => {
  assert.equal(page.chatSourceTime(0, cuts), 1);
  assert.equal(page.chatOutputTime(6, cuts), 3);
  assert.equal(page.chatSourceTime(3, cuts), 7);
  for (const source of [1.1, 3.99, 7.2, 8.99]) {
    assert.ok(Math.abs(page.chatSourceTime(page.chatOutputTime(source, cuts), cuts) - source) < 1e-8);
  }
  assert.equal(page.chatOutputTime(10, cuts), 5);
  assert.equal(page.chatSourceTime(3), 3);
});

test('each cut can be auditioned and independently restored or re-enabled', () => {
  const segment = {source_start:20, source_end:30, revision:2, subtitles_revision:2, chat_kind:'chat',
    chat_draft:{cuts:[{start:1,end:3,enabled:true,kind:'pause'}, {start:5,end:9,enabled:false,kind:'pause'}]}};
  const html = page.chatEditHTML(segment);
  assert.match(html, /原片 10 秒 → 预览 8 秒/);
  assert.match(html, /data-chat-toggle="0">恢复这段/);
  assert.match(html, /data-chat-toggle="1">重新剪掉/);
  assert.match(html, /听剪点/);
  assert.match(html, /采用这版并生成成片/);
  assert.doesNotMatch(page.chatEditHTML({...segment,chat_cuts:[[1,3]]}), /id="chat-apply"/);
  assert.equal(page.chatEditHTML({...segment,chat_kind:'other'}), '');
  assert.equal(page.hasChatPreview({...segment,publish_approval:true}), false);
});
