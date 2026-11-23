"""Quantified copper proposal, not a waiver or physical current certificate."""
import json
from pathlib import Path
from pcb_mcp import short_escapes,copper
root=Path('work/riding-intercom');s=json.loads((root/'data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text());nets={n['name']:n['id'] for n in s['nets'].values()};refs={c['ref']:c['id'] for c in s['components'].values()}
rules=[]
for net,width,current in [('DC5V',.6,1.4),('BAT',.8,2.7),('VSYS',.8,2.7),('V5',.6,1.2),('D3V3',.35,.7),('A3V3',.2,.12),('GND',.15,3.),('MIC_BIAS',.15,.01),('BIAS34',.15,.01),('BOOST_SW',.8,2.7),('SPK_P',.25,1.),('SPK_N',.25,1.)]:
 rules.append(dict(net_id=nets[net],min_width_mm=width,design_current_a=current,reference_drop_budget_v=.2,return_model='stitched_ground_planes' if net=='GND' else 'tracks',pad_escapes=[]))
byname={s['nets'][r['net_id']]['name']:r for r in rules}
for net,ref,pins,radius,length in [('DC5V','U4',['13'],1.75,1.7),('BAT','U4',['2','3'],1.75,3.),('VSYS','U4',['5','10','11'],1.75,4.5),('VSYS','U5',['3'],1.75,1.6),('V5','U5',['6'],1.75,1.6),('V5','U6',['1','2','3'],1.2,2.),('BOOST_SW','U5',['5'],1.25,1.2)]:
 byname[net]['pad_escapes'].append(dict(component_id=refs[ref],pins=pins,min_width_mm=.25,half_window_mm=radius,max_total_length_mm=length,reason='Fine-pitch physical pad exit only. Test entire declared net current at measured neck width/total length; wide main conductors retain independent width/current rules. Fixed-end thermal reference is not actual temperature qualification.'))
policy=dict(rules=rules,copper_thickness_um=35.,temperature_rise_c=20.,origin='proposed_assumption',reason='1oz prototype reference. Battery2.5753A declared DC calculation rounded to2.7A conductor screen. BOOST_SW explicit RMS current screen and speaker1A RMS. Finite pad necks and stitched ground geometry are separate models; no physical thermal/IR-drop pass claimed.')
s['pcb_copper_policy']=policy
assert not copper.policy_findings(s),copper.policy_findings(s)
(root/'hardware-circuit/copper-policy-proposal.json').write_text(json.dumps(policy,indent=2));print('Validated policy proposal',len(rules),'net rules',len(short_escapes.definitions(s)),'finite neck groups')
