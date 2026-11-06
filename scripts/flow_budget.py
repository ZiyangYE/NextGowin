"""One shared wall-clock deadline for an own synthesis/P&R attempt."""
import time
import math

_active=None


class Budget:
    def __init__(self,seconds,prior_seconds=0):
        if isinstance(seconds,bool) or not isinstance(seconds,int) or not 0<seconds<=3600:
            raise ValueError('Total flow budget must be an integer within one hour')
        if isinstance(prior_seconds,bool) or not isinstance(prior_seconds,(int,float)) or not math.isfinite(prior_seconds) or prior_seconds<0:
            raise ValueError('Retained stage cost must be finite and nonnegative')
        self.seconds=seconds;self.prior=prior_seconds;self.start=time.monotonic()

    def used(self):return self.prior+time.monotonic()-self.start

    def remaining(self):
        value=self.seconds-self.used()
        if value<=0:raise TimeoutError('Total synthesis and P&R exceeded the one-hour flow budget')
        return value

    def timeout(self,requested,reserve=0):
        value=min(float(requested),self.remaining()-reserve)
        if value<=0:raise TimeoutError('Insufficient total flow budget to start the next operation')
        return value


def activate(budget):
    global _active
    _active=budget


def clipped(requested):return _active.timeout(requested) if _active else requested
