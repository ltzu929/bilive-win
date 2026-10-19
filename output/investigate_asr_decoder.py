import sys,json,logging
from pathlib import Path
from dataclasses import asdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from bilive.config.settings import load_settings
from bilive.vendor.cuda_runtime import configure_cuda_dll_search_paths
from bilive.lifecycle.profiles import profile_for_source,subtitle_hotwords_for_profile
from faster_whisper import WhisperModel,BatchedInferencePipeline
from faster_whisper.audio import decode_audio
logging.basicConfig(level=logging.INFO)
root=Path(__file__).resolve().parents[1]
out=root/'output/asr-gap-20261016'
s=load_settings(root/'.secrets/runtime.toml')
configure_cuda_dll_search_paths()
model=WhisperModel(s.subtitle.asr_model,device=s.subtitle.asr_device,compute_type=s.subtitle.asr_compute_type,cpu_threads=2,local_files_only=True)
audio=decode_audio(str(out/'audio.wav'))
rel='8792912 - 咩栗/blive_8792912_2026-10-16-185650.mp4'
hotwords=', '.join(subtitle_hotwords_for_profile(profile_for_source(root/'Videos',rel))) or None
records=[]
class TracedBatch(BatchedInferencePipeline):
    def generate_segment_batched(self,features,tokenizer,options):
        enc,outputs=super().generate_segment_batched(features,tokenizer,options)
        for v in outputs:
            records.append({'tokens':v['tokens'],'decoded':tokenizer.tokenizer.decode(v['tokens'],skip_special_tokens=False),'avg_logprob':v['avg_logprob'],'no_speech_prob':v['no_speech_prob']})
        return enc,outputs
options=dict(language='zh',vad_filter=True,vad_parameters={'min_silence_duration_ms':2000,'speech_pad_ms':400},without_timestamps=False,word_timestamps=True,hotwords=hotwords)
for name,extra in [('current',{}),('no_hotwords',{'hotwords':None}),('temperature_fallback_candidate',{'temperature':0.2})]:
    records.clear()
    engine=TracedBatch(model)
    iterator,info=engine.transcribe(audio,batch_size=2,**(options|extra))
    segments=[asdict(v) for v in iterator]
    result={'raw_decoder':records.copy(),'segments':segments}
    (out/f'decoder_{name}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(name,json.dumps({'raw':records,'first_segments':[{k:v[k] for k in ('start','end','text')} for v in segments[:15]]},ensure_ascii=True),flush=True)
