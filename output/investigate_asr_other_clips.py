exec(open('output/investigate_asr_decoder.py',encoding='utf-8').read().split('for name,extra in ')[0])
import tempfile,itertools,re,difflib
from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import extract_audio
from faster_whisper.vad import get_speech_timestamps,VadOptions
tempfile.tempdir=str(out)
samples=[]
for p in sorted((root/'artifacts/segments').glob('*.json')):
    segment=json.loads(p.read_text(encoding='utf-8'))
    duration=segment['source_end']-segment['source_start']
    if segment.get('final_revision') is not None and (root/'Videos'/segment['source_relpath']).is_file() and 30<=duration<=180:
        samples.append(segment)
results=[]
def longest_uncovered(voice,cues):
    longest=0
    for a,b in voice:
        cursor=a
        for x,y in sorted(cues):
            if y<=cursor or x>=b:continue
            longest=max(longest,max(0,min(x,b)-cursor))
            cursor=max(cursor,min(y,b))
        longest=max(longest,b-cursor)
    return round(longest,3)
for index,segment in enumerate(samples):
    rel=segment['source_relpath']
    wav=Path(extract_audio(str(root/'Videos'/rel),start_seconds=segment['source_start'],duration_seconds=segment['source_end']-segment['source_start']))
    sample_audio=decode_audio(str(wav))
    wav.unlink()
    words=', '.join(subtitle_hotwords_for_profile(profile_for_source(root/'Videos',rel))) or None
    voice=[(v['start']/16000,v['end']/16000) for v in get_speech_timestamps(sample_audio,VadOptions(min_silence_duration_ms=2000,speech_pad_ms=400,max_speech_duration_s=30))]
    item={'sample':index+1,'source':rel,'start':segment['source_start'],'end':segment['source_end'],'state':segment['state'],'hotwords':words,'runs':{}}
    for name,hot in [('with_hotwords',words),('without_hotwords',None)]:
        records.clear()
        iterator,info=TracedBatch(model).transcribe(sample_audio,batch_size=2,**(options|{'hotwords':hot}))
        segments=[asdict(v) for v in iterator]
        maximum=max((max(len(list(g)) for _,g in itertools.groupby(v['tokens'])) for v in records),default=0)
        summary={'cues':len(segments),'chars':sum(len(re.sub(r'\s','',v['text'])) for v in segments),'max_identical_token_run':maximum,'longest_uncovered_vad_seconds':longest_uncovered(voice,[(v['start'],v['end']) for v in segments])}
        item['runs'][name]={'summary':summary,'segments':segments,'raw_decoder':records.copy()}
        print(json.dumps({'sample':index+1,'source':rel,'mode':name,**summary},ensure_ascii=True),flush=True)
    texts=[''.join(v['text'] for v in item['runs'][key]['segments']) for key in ['with_hotwords','without_hotwords']]
    item['text_similarity']=round(difflib.SequenceMatcher(None,*texts,autojunk=False).ratio(),3)
    results.append(item)
    (out/'other_clips.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
