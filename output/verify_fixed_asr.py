import sys,json,tempfile,logging,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from bilive.config.settings import load_settings
from bilive.analysis.pipeline import AnalysisPipeline
from bilive.lifecycle.profiles import profile_for_source,subtitle_hotwords_for_profile
from bilive.vendor.autoslice.mllm_sdk.audio_analyzer import unload_asr_models
logging.basicConfig(level=logging.INFO)
root=Path(__file__).resolve().parents[1]
out=root/'output/asr-gap-20261016'
tempfile.tempdir=str(out)
settings=load_settings(root/'.secrets/runtime.toml')
pipeline=AnalysisPipeline(settings,None,None)
samples=[('today','8792912 - 咩栗/blive_8792912_2026-10-16-185650.mp4',5509,5644),('historical','8792912 - 咩栗/blive_8792912_2026-10-09-191606.mp4',4010.09,4089.57)]
results=[]
try:
    for name,rel,start,end in samples:
        started=time.monotonic()
        result=pipeline._transcribe(root/'Videos'/rel,hotwords=subtitle_hotwords_for_profile(profile_for_source(root/'Videos',rel)),start_seconds=start,duration_seconds=end-start)
        assert not result.get('error'),result.get('error')
        assert result['mode']=='sequential'
        lines=result['segments']
        if name=='today':
            assert sum(1 for v in lines if 3.4<v['start']<34.4)>=6
            assert any('你也不简单' in v['text'] for v in lines)
        else:
            assert any('体谅' in v['text'] for v in lines)
            assert not any('咩栗,咩栗,小棉花' in v['text'] for v in lines)
        results.append({'sample':name,'elapsed':round(time.monotonic()-started,2),**result})
        (out/'fixed_adapter.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'sample':name,'mode':result['mode'],'lines':len(lines),'verified':True},ensure_ascii=True),flush=True)
finally:
    unload_asr_models()
