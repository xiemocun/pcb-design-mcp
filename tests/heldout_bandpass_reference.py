"""Independent continuous-frequency reference for the frozen passive bandpass."""
import hashlib,json,math
from pathlib import Path

root=Path('work/generalization/heldout-01')
run=root/'bandpass-run-01'
plan=json.loads((run/'plan.json').read_text())
assert {c['ref']:c['value'] for c in plan['components'] if c['ref'][0] in 'RC'}=={'R1':'10k','R2':'10k','C1':'100n','C2':'100n'}
expected={'VCC':{('J1','1'),('C1','1')},'MID':{('C1','2'),('R1','1'),('R2','1')},'OUT':{('R2','2'),('C2','1'),('J2','1')},'GND':{('J1','2'),('J2','2'),('R1','2'),('C2','2')}}
assert {n['name']:{(p['ref'],p['number']) for p in n['pins']} for n in plan['nets']}==expected
task=next(c for c in json.loads((root/'requirements.json').read_text())['cases'] if c['id']=='passive_bandpass')
tau=10000.*100e-9
def gain(f):
    x=2*math.pi*f*tau
    return x/math.sqrt((1-x*x)**2+9*x*x)
rows=[]
for lo,hi,glo,ghi in task['bands']:
    points=[lo,hi];peak=1/(2*math.pi*tau)
    if lo<=peak<=hi:points.append(peak)
    values=[gain(f) for f in points]
    row=dict(frequency_hz=[lo,hi],minimum=min(values),maximum=max(values),allowed=[glo,ghi],passed=min(values)>=glo and max(values)<=ghi)
    assert row['passed'];rows.append(row)
report=dict(passed=True,plan_sha256=hashlib.sha256((run/'plan.json').read_bytes()).hexdigest(),scope='Nominal ideal unloaded R/C values, continuous frequency reference, not component tolerance or parasitic proof',derivation='KCL at MID and OUT gives H(s)=s*tau/((s*tau)^2+3*s*tau+1). For x=omega*tau, |H|^2=x^2/(x^4+7*x^2+1). Its derivative has sign(1-x^4), so each closed frequency interval has extrema only at endpoints or x=1.',dc_output_v=0.,bands=rows)
(root/'bandpass-continuous-reference.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
