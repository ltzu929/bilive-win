import base64
from types import SimpleNamespace

import pytest

from bilive.vendor.autoslice.analysis_result import TranscriptSegment
from bilive.vendor.autoslice.transcript_correct import correct_transcript_segments


def test_confirmed_names_reach_correction_prompt_without_changing_cues(monkeypatch):
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-placeholder')
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"corrections":[]}'))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    lines = [TranscriptSegment(0.2, 1.4, '白露')]
    result = correct_transcript_segments(lines, artist='主播甲', hotwords=['白露', '匹诺康尼'],
        client_factory=lambda **kw: client)
    prompt = captured[0]['messages'][1]['content']
    assert captured[0]['model'] == 'mimo-v2.6-flash'
    assert captured[0]['max_completion_tokens'] == 2048
    assert captured[0]['extra_body'] == {'thinking': {'type': 'disabled'}}
    assert captured[0]['response_format'] == {'type': 'json_object'}
    assert '["白露", "匹诺康尼"]' in prompt
    assert [(v.start, v.end, v.text) for v in result] == [(0.2, 1.4, '白露')]


def test_audio_correction_applies_name_edits_and_keeps_other_words(monkeypatch, tmp_path):
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-placeholder')
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'isolated audio fixture')
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=
            '{"corrections":[{"index":0,"text":"偶尔如果说咩栗在里面艾特我的话"},'
            '{"index":1,"text":"因为我没有去找他们"}]}'))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    lines = [TranscriptSegment(1.2, 3.4, '偶尔如果说麦尔在里面我的话'),
             TranscriptSegment(4, 5, '因为我没有找完')]
    result = correct_transcript_segments(lines, artist='呜米', hotwords=['呜米', '咩栗'],
        audio_path=audio, client_factory=lambda **kw: client)
    assert [(v.start, v.end, v.text) for v in result] == [
        (1.2, 3.4, '偶尔如果说咩栗在里面我的话'), (4, 5, '因为我没有找完')]
    assert [v.text for v in lines] == ['偶尔如果说麦尔在里面我的话', '因为我没有找完']
    content = captured[0]['messages'][1]['content']
    assert content[0] == {'type': 'input_audio', 'input_audio': {
        'data': 'data:audio/wav;base64,' + base64.b64encode(audio.read_bytes()).decode('ascii')}}
    assert '1.20' in content[1]['text'] and '3.40' in content[1]['text']


@pytest.mark.parametrize('before,after,name,expected', [
    ('那些人都是麦老师的朋友', '那些人都是咩栗老师的朋友', '咩栗', '那些人都是咩栗老师的朋友'),
    ('我跟白鹿很熟，昨天一起玩', '我和白露很熟，昨天一起聊', '白露', '我跟白露很熟，昨天一起玩'),
    ('我跟白鹿很熟', '我和白露特别熟悉', '白露', '我跟白鹿很熟'),
    ('咩栗在里面我的话', '咩栗在里面艾特我的话', '咩栗', '咩栗在里面我的话'),
    ('白露老师说话了', '白露说话了', '白露', '白露老师说话了'),
])
def test_name_edit_filter_preserves_the_original_sentence(before, after, name, expected):
    from bilive.vendor.autoslice.transcript_correct import _keep_name_edits
    assert _keep_name_edits(before, after, [name]) == expected


def test_missing_correction_audio_keeps_original_without_sending_request(monkeypatch, tmp_path):
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-placeholder')
    lines = [TranscriptSegment(0, 1, '原字幕')]
    result = correct_transcript_segments(lines, hotwords=['咩栗'], audio_path=tmp_path / 'missing.wav',
        client_factory=lambda **kw: pytest.fail('missing audio must not be submitted'))
    assert result == lines
