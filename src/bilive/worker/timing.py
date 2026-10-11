"""Wall durations of actual processing stages, including failed attempts."""
import time
from contextlib import contextmanager


@contextmanager
def timed_stage(record,stage,on_change=None):
    started=time.perf_counter()
    timing={'status':'processing','started_at':time.time()}
    record.setdefault('timings',{})[stage]=timing
    if on_change:
        on_change()
    try:
        yield
    except BaseException:
        timing['status']='failed'
        raise
    else:
        timing['status']='done'
    finally:
        timing['elapsed_seconds']=round(time.perf_counter()-started,4)
        if on_change:
            on_change()
