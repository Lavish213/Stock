"""Monotonic worker progress checks; does not certify source authentication."""
import threading,time
from lavish_core.utils.safety import finite_number

class WorkerHealth:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.lock=threading.Lock();self.workers={}

    def register(self,name,max_age):
        age=finite_number(max_age,positive=True)
        with self.lock:self.workers[name]=(self.clock(),age)

    def beat(self,name):
        with self.lock:
            if name in self.workers:
                _,age=self.workers[name];self.workers[name]=(self.clock(),age)

    def status(self,alive=None):
        reasons=[];now=self.clock()
        with self.lock:
            for name,(last,age) in self.workers.items():
                if alive is not None and alive.get(name) is not True:reasons.append(name+':dead')
                if now<last or now-last>age:reasons.append(name+':no_progress')
        return {'healthy':not reasons,'reasons':reasons}

WORKER_HEALTH=WorkerHealth()
