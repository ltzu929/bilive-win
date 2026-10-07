const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({ Date });
vm.runInContext(fs.readFileSync('frontend/ui.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('frontend/workbench.js', 'utf8'), context);
const state = (job, evidence, segments = []) => {
  context.input = { job, evidence, segments };
  return vm.runInContext('recordingAnalysisState(input.job, input.evidence, input.segments)', context);
};
test('queued and processing are never presented as empty completed results', () => {
  assert.match(state({status: 'pending', progress: {reason: 'ASR model missing'}}).detail, /ASR model missing/);
  const busy = state({status: 'processing', progress: {started_at: Date.now()/1000-65}},
    {total: 3, summary: {completed: 1}});
  assert.match(busy.title, /已用 00:01:05/);
  assert.match(busy.detail, /1 \/ 3/);
  assert.match(busy.empty, /尚未结束/);
});
test('no recall, explicit drops, technical failure and skipped analysis have different outcomes', () => {
  assert.match(state({status: 'done'}, {total: 0}).title, /未召回/);
  assert.match(state({status: 'done'}, {total: 2, summary: {dropped: 2}}).title, /AI 未建议保留/);
  assert.match(state({status: 'done'}, {total: 2, summary: {failed: 1}}).title, /人工检查/);
  assert.match(state({status: 'failed', result: {error: 'timeout'}}).detail, /timeout/);
  assert.match(state({status: 'failed'}).empty, /不能据此判断/);
  assert.match(state({status: 'done'}, {skipped: true, skip_reason: 'below min_source_size_mb'}).title, /跳过/);
  assert.match(state({status: 'done', result: {cancelled: true}}).title, /取消/);
});
test('partial evidence pages cannot falsely report all candidates dropped', () => {
  const result = state({status: 'done'}, {total: 30, candidates: [{state: 'drop'}]}, [{state: 'needs_review'}]);
  assert.match(result.title, /1 个片段可审核/);
});
test('completed AI judgment does not hide subtitle or rendering failure', () => {
  const result = state({status: 'done'}, {total: 1, summary: {},
    segments: [{error: 'PipelineError', timings: {formal_asr: {status: 'failed'}}}]}, [{state: 'needs_review'}]);
  assert.match(result.title, /人工检查/);
  assert.match(result.detail, /1 个片段在字幕 \/ 成片阶段失败/);
});
