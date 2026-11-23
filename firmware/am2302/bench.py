"""Capture UART without flashing; check a marked mainboard bench session offline.

Serial observations cannot establish supply voltage, sensor accuracy or physical
production qualification. Synthetic fixtures always retain their origin label.
"""
import argparse,hashlib,json,math,select,sys,time
from pathlib import Path

PHASES=('connected','fault','recovered')
MIN_SECONDS={'connected':600.,'fault':60.,'recovered':60.}
MIN_SAMPLES={'connected':200,'fault':15,'recovered':15}
ERROR_REASONS={'no_response_preamble','truncated_frame','pulse_timing','checksum','out_of_range'}

def number(x):return type(x) in (int,float) and math.isfinite(x)
def integer(x):return type(x) is int and x>=0

def analyze(records,expected):
    errors=[];missing=[];phases=[];samples=[];boots=[];raw_count=0;last_t=-1.;end=None
    origin=records[0].get('origin') if records and isinstance(records[0],dict) else None
    if origin not in ('uart_capture','synthetic'):errors.append('UNKNOWN_EVIDENCE_ORIGIN')
    if not records or not isinstance(records[0],dict) or records[0].get('type')!='session':errors.append('MISSING_SESSION_START')
    for index,r in enumerate(records):
        if not isinstance(r,dict):errors.append('INVALID_RECORD');continue
        t=r.get('host_elapsed_s')
        if not number(t) or t<0 or t<last_t:errors.append('INVALID_HOST_TIME');continue
        last_t=t;kind=r.get('type')
        if end is not None:errors.append('RECORD_AFTER_END')
        if kind=='session':
            if index:errors.append('MULTIPLE_SESSION_STARTS')
        elif kind=='phase':
            if r.get('name') not in PHASES:errors.append('INVALID_PHASE')
            else:phases.append((r['name'],t))
        elif kind=='end':end=t
        elif kind=='serial':
            raw_count+=1;raw=r.get('text')
            if not isinstance(raw,str):errors.append('INVALID_SERIAL_TEXT');continue
            try:msg=json.loads(raw)
            except (ValueError,TypeError):
                if raw.lstrip().startswith('{'):errors.append('MALFORMED_JSON_FRAME')
                # IDF/ROM boot text is preserved but not interpreted as a sample.
                continue
            if not isinstance(msg,dict):errors.append('INVALID_JSON_FRAME');continue
            if msg.get('event')=='boot':boots.append((t,msg))
            elif 'sequence' in msg:samples.append((t,msg))
            else:errors.append('UNKNOWN_JSON_FRAME')
        else:errors.append('UNKNOWN_RECORD_TYPE')
    if [p[0] for p in phases]!=list(PHASES):missing.append('THREE_ORDERED_PHASES_REQUIRED')
    if end is None:missing.append('SESSION_NOT_FINISHED')
    if not boots:missing.append('BOOT_RECORD_MISSING')
    if len(boots)>1:errors.append('UNEXPECTED_REBOOT')
    for _,b in boots:
        for k in ('project_id','revision','pcb_build_id','app_elf_sha256','drive_gpio','read_gpio'):
            if k not in expected or b.get(k)!=expected[k]:errors.append('BOOT_IDENTITY_MISMATCH_'+k.upper())
        if type(b.get('protocol')) is not int or b['protocol']!=1:errors.append('BOOT_PROTOCOL_MISMATCH')
        if type(b.get('reset_reason')) is not int or b['reset_reason']!=1:
            errors.append('UNEXPECTED_INITIAL_RESET_REASON')
    prev=None;valid=[]
    for t,m in samples:
        if not integer(m.get('sequence')) or not integer(m.get('uptime_ms')) or m['sequence']<1:
            errors.append('INVALID_SAMPLE_COUNTER');continue
        if m.get('status')=='ok':
            temp,hum=m.get('temperature_c'),m.get('humidity_rh')
            if not number(temp) or not number(hum) or not -40<=temp<=80 or not 0<=hum<=100:errors.append('INVALID_MEASUREMENT')
            if 'reason' in m:errors.append('AMBIGUOUS_SAMPLE')
        elif m.get('status')=='error':
            if m.get('reason') not in ERROR_REASONS:errors.append('UNKNOWN_SENSOR_ERROR')
            if 'temperature_c' in m or 'humidity_rh' in m:errors.append('STALE_VALUES_ON_ERROR')
        else:errors.append('UNKNOWN_SAMPLE_STATUS')
        if prev:
            pt,pm=prev
            if m['sequence']!=pm['sequence']+1:errors.append('SEQUENCE_GAP_OR_RESET')
            dt=m['uptime_ms']-pm['uptime_ms'];ht=t-pt
            if not 2000<=dt<=5000:errors.append('SAMPLE_INTERVAL_OR_UPTIME_RESET')
            if ht>6.0:errors.append('HOST_RECEIVE_GAP')
            if abs(ht-dt/1000)>2.:errors.append('HOST_DEVICE_TIME_DIVERGENCE')
        elif m['sequence']!=1:errors.append('INITIAL_SAMPLES_MISSING')
        prev=(t,m);valid.append((t,m))
    if boots and valid:
        boot_t=boots[0][0];first_t=valid[0][0]
        if first_t<boot_t or first_t-boot_t>10.:errors.append('FIRST_SAMPLE_DEADLINE')
        if first_t-boot_t<2.:errors.append('SENSOR_POWERUP_WAIT_TOO_SHORT')
    if not samples:missing.append('NO_SAMPLES')
    summaries=[]
    if [p[0] for p in phases]==list(PHASES) and end is not None:
        if boots and not phases[0][1]<=boots[0][0]<phases[1][1]:errors.append('BOOT_OUTSIDE_CONNECTED_PHASE')
        for i,(phase,start) in enumerate(phases):
            stop=phases[i+1][1] if i+1<len(phases) else end
            # 5s transition grace permits the in-flight frame to finish.
            selected=[(t,m) for t,m in valid if start+5.<=t<stop]
            if stop-start<MIN_SECONDS[phase]:missing.append('PHASE_TOO_SHORT_'+phase.upper())
            if len(selected)<MIN_SAMPLES[phase]:missing.append('INSUFFICIENT_SAMPLES_'+phase.upper())
            wanted='error' if phase=='fault' else 'ok'
            bad=sum(m.get('status')!=wanted for _,m in selected)
            if bad:errors.append('PHASE_STATUS_MISMATCH_'+phase.upper())
            if selected and (selected[0][0]>start+10. or stop-selected[-1][0]>6.):errors.append('PHASE_SILENCE_'+phase.upper())
            summaries.append(dict(phase=phase,duration_s=stop-start,samples=len(selected),unexpected_samples=bad))
    return dict(verdict='fail' if errors else 'incomplete' if missing else 'records_match_protocol',
                evidence_origin=origin,errors=sorted(set(errors)),missing=sorted(set(missing)),
                phases=summaries,boot_count=len(boots),sample_count=len(samples),raw_line_count=raw_count,
                scope='UART identity, counters, timing and response to operator-marked fault; no voltage or accuracy measurement',
                physical_function_qualified=False,manufacturing_approved=False)

def capture(port,path,seconds):
    import serial
    # Explicitly avoid automatic reset/bootloader toggling. RTS/DTR must be unwired.
    uart=serial.Serial(port=None,baudrate=115200,timeout=.1)
    uart.dtr=False;uart.rts=False;uart.port=port
    phase=0;start=time.monotonic();buffer=b''
    with path.open('x') as f:
        def emit(kind,**kw):
            f.write(json.dumps(dict(type=kind,host_elapsed_s=time.monotonic()-start,**kw),ensure_ascii=False)+'\n');f.flush()
        emit('session',origin='uart_capture',port=port,baudrate=115200)
        emit('phase',name=PHASES[phase])
        print('开始记录后给主板上电。保持 connected 至少 600 秒。执行故障注入后输入 fault，撤除后输入 recovered，最后输入 stop。每个后续阶段至少 60 秒。',flush=True)
        uart.open()
        try:
            while time.monotonic()-start<seconds:
                buffer+=uart.read(4096)
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1);emit('serial',text=line.decode('utf-8',errors='replace').rstrip('\r'))
                if len(buffer)>65536:raise ValueError('Serial line exceeded 64 KiB')
                if select.select([sys.stdin],[],[],0)[0]:
                    cmd=sys.stdin.readline().strip()
                    if cmd=='stop':break
                    if phase+1<len(PHASES) and cmd==PHASES[phase+1]:
                        phase+=1;emit('phase',name=cmd);print('已标记 '+cmd,flush=True)
                    elif cmd:print('下一标记应为 '+(PHASES[phase+1] if phase+1<len(PHASES) else 'stop'),flush=True)
        finally:
            if buffer:emit('serial',text=buffer.decode('utf-8',errors='replace'))
            emit('end');uart.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='command',required=True)
    cap=subs.add_parser('capture');cap.add_argument('--port',required=True);cap.add_argument('--output',type=Path,required=True);cap.add_argument('--seconds',type=float,default=1200)
    check=subs.add_parser('analyze');check.add_argument('--log',type=Path,required=True);check.add_argument('--expected',type=Path,required=True);check.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='capture':
        if not math.isfinite(args.seconds) or args.seconds<=0:parser.error('seconds must be positive and finite')
        capture(args.port,args.output,args.seconds)
    else:
        raw=args.log.read_bytes();records=[json.loads(line) for line in raw.splitlines() if line.strip()]
        result=analyze(records,json.loads(args.expected.read_text()));result['log_sha256']=hashlib.sha256(raw).hexdigest()
        with args.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        sys.exit(0 if result['verdict']=='records_match_protocol' else 2)
if __name__=='__main__':main()
