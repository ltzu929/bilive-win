import json
from types import SimpleNamespace

import pytest

from bilive.analysis.metadata import generate_metadata


def response(content, finish='stop'):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])


def call(monkeypatch, content, captured=None, finish='stop'):
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-test-key')
    captured = captured if captured is not None else {}
    def factory(**kwargs):
        captured['client'] = kwargs
        def create(**request):
            captured['request'] = request
            return response(content, finish)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return generate_metadata([{'start': 0.1, 'end': 1.2, 'text': '之后可能考虑提前收集点歌'}],
        artist='呜米', duration=2, model='mimo-v2.6-flash', base_url='https://example.invalid/v1',
        timeout=10, client_factory=factory)


def test_generates_valid_metadata_using_only_given_subtitles(monkeypatch):
    captured = {}
    result = call(monkeypatch, json.dumps({'title':'  提前点歌的想法  ', 'description':'考虑提前收集点歌。',
                                          'tags':[' 点歌 ', '点歌']}), captured)
    assert result == {'title':'提前点歌的想法', 'description':'考虑提前收集点歌。', 'tags':['点歌']}
    request = captured['request']
    assert request['messages'][1]['content'].count('之后可能考虑提前收集点歌') == 1
    assert '0.10–1.20s' in request['messages'][1]['content']
    assert all(isinstance(message['content'], str) for message in request['messages'])
    assert request['extra_body'] == {'thinking': {'type': 'disabled'}}
    assert captured['client']['max_retries'] == 0


@pytest.mark.parametrize('content', ['not JSON', '[]', '{}',
    json.dumps({'title': 3, 'description': '说明', 'tags': []}),
    json.dumps({'title': '标题', 'description': '说明', 'tags': ['甲,乙']}),
    json.dumps({'title': '标题', 'description': '说明', 'tags': '文字'}),
    json.dumps({'title': '标题', 'description': None, 'tags': []})])
def test_rejects_invalid_model_metadata(monkeypatch, content):
    with pytest.raises(ValueError):
        call(monkeypatch, content)


def test_rejects_incomplete_response_and_missing_key(monkeypatch):
    with pytest.raises(ValueError, match='完整文案'):
        call(monkeypatch, '{}', finish='length')
    monkeypatch.delenv('MIMO_API_KEY')
    with pytest.raises(ValueError, match='未配置'):
        generate_metadata([], artist='呜米', duration=2, model='test', base_url='https://example.invalid', timeout=1)


def test_metadata_preflight_needs_no_asr_or_ffmpeg(monkeypatch):
    from bilive.worker.preflight import check_action
    monkeypatch.setenv('MIMO_API_KEY', 'isolated-test-key')
    monkeypatch.setattr('shutil.which', lambda name: None)
    assert check_action(SimpleNamespace(action='generate_metadata')) == (True, '')
    monkeypatch.delenv('MIMO_API_KEY')
    assert check_action(SimpleNamespace(action='generate_metadata'))[0] is False
