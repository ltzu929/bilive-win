"""Wall durations of actual processing stages, including failed attempts."""
import time
from contextlib import contextmanager


@contextmanager
def timed_stage(record,stage):
    started=time.perf_counter()
    timing={'status':'processing'}
    record.setdefault('timings',{})[stage]=timing
    try:
        yield
    except BaseException:
        timing['status']='failed'
        raise
    else:
        timing['status']='done'
    finally:
        timing['elapsed_seconds']=round(time.perf_counter()-started,4)
