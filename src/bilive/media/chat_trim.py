"""Find removable waits in a chat clip. All returned positions are clip-relative."""
from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from pathlib import Path

from bilive.media.process import run_media_command


def chat_kind(segment, folder: Path):
    info = segment
    if 'clip_type' not in info and (folder / 'analysis_sidecar.json').is_file():
        try:
            info = json.loads((folder / 'analysis_sidecar.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            info = segment
    label = f"{info.get('content_type', '')} {info.get('clip_type', '')}".lower()
    if re.search(r'singing|music|dance|gameplay|演唱|翻唱|唱歌|歌回|舞蹈|跳舞|游戏实况', label):
        return 'other'
    if re.search(r'\bchat\b|chat_|_chat|聊天|杂谈|闲聊', label):
        return 'chat'
    return 'unknown'


def _gaps(duration, occupied):
    cursor = 0.0
    for start, end in sorted(occupied):
        if start > cursor:
            yield cursor, min(start, duration)
        cursor = max(cursor, end)
    if cursor < duration:
        yield cursor, duration


def _reaction_times(messages):
    times = []
    prior = None
    for at, text in sorted(messages):
        if re.search(r'哈|笑|[?？!！]{2,}|233|666|www|^草$', text, re.I):
            times.append(at)
        if prior is not None and at - prior <= 2:
            times.extend((prior, at))
        prior = at
    return sorted(times)


def suggest_cuts(duration, lines, speech, activity, messages, focus=None):
    occupied = [(line['start'], line['end']) for line in lines] + speech
    reactions = _reaction_times(messages)
    activity = sorted(activity)
    prompts = [line['end'] for line in lines if re.search(
        r'你们猜|猜猜|猜一下|等.{0,4}(弹幕|回答)|弹幕.{0,6}(扣|打个|打出来)|你们觉得.{0,8}[？?吗呢]', line['text'])]
    cuts = []
    for a, b in _gaps(duration, occupied):
        head, tail = a == 0, b == duration
        if b-a < (1.6 if head or tail else 3.0) or (head and tail):
            continue
        # Keep the intake of breath, the start of the wait and the reaction tail.
        start = 0.0 if head else round(a + (.65 if tail else .75), 3)
        end = duration if tail else round(b - (.45 if head else .65), 3)
        if any(a-1 <= t <= a+.1 for t in prompts):
            continue
        if bisect_left(reactions, a-.25) < bisect_right(reactions, b+1.0):
            continue
        if focus is not None and start-.6 <= focus <= end+.6:
            continue
        if bisect_left(activity, start) < bisect_right(activity, end):
            continue
        cuts.append({'start': start, 'end': end, 'kind': 'head' if head else 'tail' if tail else 'pause',
                     'enabled': True})
    return cuts


def _decode(source, start, duration, options):
    proc = run_media_command(['ffmpeg', '-v', 'error', '-ss', f'{start:.6f}', '-i', str(source),
                              '-t', f'{duration:.6f}', *options, 'pipe:1'], capture_output=True)
    if proc.returncode:
        raise ValueError('精剪无法读取片段音画：' + proc.stderr.decode('utf-8', 'replace')[-400:])
    return proc.stdout


def detect_chat_cuts(source, segment, lines):
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    from bilive.analysis.pipeline import read_danmaku

    start = segment['source_start']
    duration = segment['source_end'] - start
    if not suggest_cuts(duration, lines, [], [], []):
        return []
    xml = source.with_suffix('.xml')
    messages = [(t-start, text) for t, text in read_danmaku(xml)
                if start-2 <= t <= start+duration+2] if xml.is_file() else []
    focus = segment.get('editorial_suggestion', {}).get('frame')
    focus = focus-start if focus is not None else None
    speech, activity = [], []
    # Bound decoded memory even when someone manually selects a long recording.
    # Overlap preserves utterances and movement at analysis-window boundaries.
    for offset in range(0, int(np.ceil(duration)), 90):
        origin = max(0, offset - 1)
        length = min(duration, offset+91) - origin
        audio = np.frombuffer(_decode(source, start+origin, length,
            ['-map', '0:a:0', '-ac', '1', '-ar', '16000', '-f', 's16le']), dtype='<i2')
        frames = np.frombuffer(_decode(source, start+origin, length,
            ['-map', '0:v:0', '-vf', 'fps=4,scale=96:64:flags=area,format=gray', '-f', 'rawvideo']),
            dtype=np.uint8).reshape(-1, 64, 96)
        if audio.size / 16000 < length-.3 or len(frames)/4 < length-.5:
            raise ValueError('精剪范围内的音画不完整，请检查片段出点')
        samples = audio.astype(np.float32) / 32768
        if len(samples):
            for v in get_speech_timestamps(samples, VadOptions(threshold=.38, min_speech_duration_ms=90,
                    min_silence_duration_ms=350, speech_pad_ms=140)):
                speech.append((origin+v['start']/16000, min(duration, origin+v['end']/16000)))
        # Sudden non-verbal sounds (e.g. a laugh or sound effect) over steady BGM.
        blocks = samples[:len(samples)//3200*3200].reshape(-1, 3200)
        rms = np.sqrt(np.mean(blocks**2, axis=1))
        for i, level in enumerate(rms):
            if level > .025 and level > 2.4*np.median(rms[max(0,i-8):i+9]):
                activity.append(origin+(i+.5)*.2)
        # Tile differences also see a small avatar reacting against a static backdrop.
        if len(frames) > 1:
            delta = np.abs(np.diff(frames.astype(np.int16), axis=0))
            tiles = delta.reshape(-1, 8, 8, 12, 8).mean(axis=(2, 4))
            moving = (tiles.max(axis=(1, 2)) > 30) | ((tiles > 15).sum(axis=(1, 2)) >= 2)
            activity.extend(origin+(int(i)+1)/4 for i in np.flatnonzero(moving))
    return suggest_cuts(duration, lines, speech, activity, messages, focus)
