"""Numeric-only upload telemetry; never emit curl responses, URLs or stderr."""
from contextlib import contextmanager
import math
import re
import subprocess
import tempfile
import threading
import time

_LABELS={'Streamtape','StreamHG','Abyss','Telegram'}
_STATS='\nCLIXARENA_UPLOAD_STATS:'


def file_size(path):
    try:return path.stat().st_size
    except (OSError,AttributeError):return None


def _number(value):
    result=float(value)
    if not math.isfinite(result) or result<0:raise ValueError('Invalid numeric telemetry')
    return result


def _amount(value):
    match=re.fullmatch(r'(\d+(?:\.\d+)?)([kKMGTPE]?)',value)
    if not match:raise ValueError('Invalid numeric telemetry')
    return float(match[1])*1024**('kMGTPE'.find(match[2].replace('K','k'))+1 if match[2] else 0)


def progress_row(line):
    fields=line.split()
    if len(fields)!=12 or any(not re.fullmatch(r'\d{1,3}',fields[i]) or int(fields[i])>100 for i in (0,2,4)):return None
    try:return int(fields[4]),_amount(fields[5])
    except ValueError:return None


def _summary(stdout,label,elapsed,ok):
    raw,separator,values=stdout.rpartition(_STATS)
    if separator:
        try:
            sent,speed,total,start,status=values.strip().split('|')
            sent,speed,total,start=map(_number,(sent,speed,total,start))
            if not re.fullmatch(r'\d{3}',status):raise ValueError()
            print(f'{label} transfer {"finished" if ok else "stopped"}: sent {sent/1024**2:.2f} MiB | average {speed/1024**2:.2f} MiB/s | transfer/request {total:.1f}s | connection/setup {start:.1f}s',flush=True)
        except (ValueError,OverflowError):
            print(f'{label} upload request elapsed: {elapsed:.1f}s; detailed transfer metrics unavailable',flush=True)
        return raw
    print(f'{label} upload request elapsed: {elapsed:.1f}s; detailed transfer metrics unavailable',flush=True)
    return stdout


def run_upload(args,*,input,capture_output=True,text=True,timeout=3610,runner=subprocess.run,label='Streamtape',size=None):
    if label not in _LABELS:raise ValueError('Unknown upload telemetry label')
    args=list(args);previous=''
    # Avoid curl's default one-second stall when an HTTP/1.1 upload server
    # does not answer Expect: 100-continue. This does not add upload retries.
    if args and args[0] == 'curl' and '--expect100-timeout' not in args:
        args += ['--expect100-timeout','0.2']
    if '--write-out' in args:
        index=args.index('--write-out');previous=args[index+1];del args[index:index+2]
    args=[value for value in args if value!='--silent']
    # Curl's meter is parsed privately. Its error output is never forwarded.
    args+=['--progress-meter','--write-out',previous+_STATS+'%{size_upload}|%{speed_upload}|%{time_total}|%{time_pretransfer}|%{http_code}']
    # Keep the multipart argument last for compatibility with existing callers.
    if '--form' in args:
        index=args.index('--form');form=args[index:index+2];del args[index:index+2];args+=form
    started=time.monotonic()
    size_text=f'{size/1024**2:.2f} MiB' if size is not None else 'unknown size'
    print(f'{label} upload starting: {size_text}',flush=True)
    if runner is not subprocess.run:
        response=runner(args,input=input,capture_output=capture_output,text=text,timeout=timeout)
        clean=_summary(response.stdout,label,time.monotonic()-started,response.returncode==0)
        return subprocess.CompletedProcess(args,response.returncode,clean,'')
    with tempfile.TemporaryFile(mode='w+b') as output:
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=output,stderr=subprocess.PIPE)
        timer=threading.Timer(timeout,process.kill);timer.daemon=True;timer.start()
        last_report=started;buffer=bytearray()
        try:
            process.stdin.write(input.encode('utf-8'));process.stdin.close()
            while True:
                byte=process.stderr.read(1)
                if not byte:break
                if byte in (b'\r',b'\n'):
                    row=progress_row(buffer.decode('ascii',errors='replace'));buffer.clear()
                    now=time.monotonic()
                    if row and now-last_report>=10:
                        percent,sent=row;elapsed=max(0.001,now-started);rate=sent/elapsed
                        eta=max(0,(size-sent)/rate) if size is not None and rate else None
                        print(f'{label} upload {percent}% | sent {sent/1024**2:.2f} MiB | average {rate/1024**2:.2f} MiB/s | elapsed {elapsed:.0f}s'+(f' | estimated remaining {eta:.0f}s' if eta is not None else ''),flush=True)
                        last_report=now
                elif len(buffer)<4096:buffer.extend(byte)
                else:buffer.clear()
            process.wait()
            elapsed=time.monotonic()-started
            if elapsed>=timeout and process.returncode:raise subprocess.TimeoutExpired('curl upload',timeout)
            output.seek(0);raw=output.read(1024**2+1)
            if len(raw)>1024**2:raise ValueError('Upload response exceeded safe size; check account before retrying')
            clean=_summary(raw.decode('utf-8',errors='replace'),label,elapsed,process.returncode==0)
            return subprocess.CompletedProcess(args,process.returncode,clean,'')
        except BaseException:
            print(f'{label} upload request stopped after {time.monotonic()-started:.1f}s; check the account before retrying',flush=True)
            raise
        finally:
            timer.cancel()
            if process.poll() is None:process.kill();process.wait()
            process.stderr.close()
            if not process.stdin.closed:process.stdin.close()


@contextmanager
def timed_stage(label):
    # Callers supply only fixed stage labels, never filenames or host responses.
    started=time.monotonic();failed=False
    print(label+' started',flush=True)
    try:yield
    except BaseException:
        failed=True;raise
    finally:print(f'{label} {"failed" if failed else "finished"}: {time.monotonic()-started:.1f}s',flush=True)


def timed_call(label,call,*args,**kwargs):
    with timed_stage(label):return call(*args,**kwargs)
