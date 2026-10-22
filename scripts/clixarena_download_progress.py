"""Numeric-only download progress, including stalled-connection heartbeats."""
import threading
import time


class DownloadProgress:
    def __init__(self,total=None,connections=1,log=print):
        self.total=total;self.connections=connections;self.log=log
        self.started=time.monotonic();self.parts={};self.lock=threading.Lock()
        self.stop=threading.Event();self.thread=None;self.finished=False

    def __enter__(self):
        self.thread=threading.Thread(target=self._heartbeat,daemon=True)
        self.thread.start()
        return self

    def __exit__(self,*args):
        self.close()

    def close(self):
        self.stop.set()
        if self.thread is not None:self.thread.join()

    def update(self,received,part=0):
        # Replace an in-flight range's count on retry, rather than count it twice.
        with self.lock:self.parts[part]=max(0,int(received))

    def emit(self):
        with self.lock:
            received=sum(self.parts.values());total=self.total
        elapsed=max(0.001,time.monotonic()-self.started);speed=received/elapsed
        if total:
            # 100% is reserved for finish(), after byte/range checks pass.
            percentage=min(99.9,received*100/total)
            amount=f'{percentage:.1f}% | received {received/1024**2:.2f}/{total/1024**2:.2f} MiB'
            eta=f' | estimated remaining {max(0,(total-received)/speed):.0f}s' if speed else ''
        else:
            amount=f'received {received/1024**2:.2f} MiB | total size unknown';eta=''
        self.log(f'Downloading {amount} | average {speed/1024**2:.2f} MiB/s | elapsed {elapsed:.0f}s | connections {self.connections}'+eta,flush=True)

    def finish(self):
        self.close()
        with self.lock:received=sum(self.parts.values())
        elapsed=max(0.001,time.monotonic()-self.started)
        self.log(f'Download 100% complete | received {received/1024**2:.2f} MiB | average {received/1024**2/elapsed:.2f} MiB/s | elapsed {elapsed:.1f}s',flush=True)
        self.finished=True

    def _heartbeat(self):
        while not self.stop.wait(10):self.emit()
