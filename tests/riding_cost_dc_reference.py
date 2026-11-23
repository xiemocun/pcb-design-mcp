"""Prepare declared worst-load DC screens; no physical/function claim or approval."""
import json,sys
from pathlib import Path
from pcb_mcp import registry,dc_power_tree
root=Path('work/riding-intercom');s=json.loads((root/'data/89184e4c1e4740f88be798dfed22a98b/state.json').read_text())
rails=[]
for name,lo,nom,hi,basis in [
 ('DC5V',4.75,5.,5.25,'External regulated 5 V +/-5 percent source; harness polarity and minimum2A required'),
 ('BAT',3.,3.7,4.2,'Declared operating battery terminal interval; protected1S4.2V pack, minimum3A discharge; cutoff behavior requires selected pack'),
 ('VSYS',2.7,3.6,4.5,'Declared lower operating bound includes0.3V battery/path sag allowance, not a guaranteed BQ dropout at3A; BQ OUT table upper4.5V'),
 ('V5',4.7,5.,5.3,'Proposed regulated rail design window; PWM feedback tolerances checked separately; PFM/transient maxima not proven'),
 ('D3V3',3.15,3.318,3.45,'Proposed digital rail DC window around0.6V*(1+453k/100k); exact divider tolerance and regulation require separate check'),
 ('A3V3',3.23,3.3,3.37,'TLV75533 3.3V 2-percent design interval, positive5V input headroom'),
 ('MIC_BIAS',0.,2.,3.6,'Internal ADC reference; capacitor-only DC load, not an external supply source'),
 ('BIAS34',0.,2.,3.6,'Internal ADC reference; capacitor-only DC load, not an external supply source')]:rails.append(dict(name=name,minimum_v=lo,nominal_v=nom,maximum_v=hi,basis=basis))
loads=[]
for ref,pins,current,why in [('U1',['2'],.5,'ESP32-S3 transmitter and processor design reserve500mA'),('U2',['6','7','22','23'],.008,'Declared8mA for each ADC supply pin,32mA aggregate'),('U3',['3','4','11'],.006,'Declared6mA for each codec supply pin,18mA aggregate'),('U8',['6'],.6,'Declared amplifier average DC600mA; transients and acoustic volume not qualified'),('MK1',['1'],.001,'Microphone supply reserve1mA'),('MK2',['1'],.001,'Microphone supply reserve1mA')]:
 for pin in pins:loads.append(dict(reference=ref,pin=pin,current_a=current,reason=why))
def stage(ref,kind,vi,vo,ip,op,ir,imax,omax,eff,reason):return dict(reference=ref,kind=kind,input_net=vi,output_net=vo,input_pins=ip,output_pins=op,input_range_v=ir,max_input_a=imax,max_output_a=omax,efficiency=eff,overhead_a=.001,path_drop_budget_v=.3 if kind=='power_path' else None,reason=reason)
stages=[
 stage('U4','power_path','BAT','VSYS',['2','3'],['10','11'],[2.5,4.2],4.5,4.5,None,'BQ24074 rated battery discharge/OUT4.5A; BAT-to-OUT sag is a declared rail allowance, not guaranteed at maximumload'),
 stage('U5','boost','VSYS','V5',['3'],['6'],[.5,5.5],2.7,None,.85,'Declared conservative DC input screen2.7A, below source valley limit reference; switching ripple/frequency/thermal need separate analysis; efficiency85percent is assumption'),
 stage('U6','buck','V5','D3V3',['1','2'],['5','6','7'],[2.4,5.5],None,1.,.85,'TPSM82821A rated1A output; efficiency85percent is conservative design assumption, not guaranteed minimum'),
 stage('U7','linear','V5','A3V3',['1'],['5'],[1.45,5.5],None,.5,None,'TLV755P rated500mA; declared5V lower4.7V leaves>1.3V headroom')]
reserves={r['name']:0. for r in rails};reserves.update(D3V3=.01,A3V3=.005,V5=.01)
source=lambda name,ref,cap:dict(net=name,reference=ref,pin='1',capacity_a=cap,reason='External '+name+' boundary actual connector; minimum rated source capacity, not board current measurement')
cases=[dict(name='battery_talk',condition='Switch on, battery in declared operating interval, maximum declared simultaneous average loads.',sources=[source('BAT','J2',3.)],stages=stages),dict(name='adapter_talk_battery_assist_upper_screen',condition='Adapter plugged and switch on. Conservatively debit full system demand to battery, giving no credit for adapter assistance; separately reserve input-current limit on adapter. Does not solve BQ current sharing or dynamics.',sources=[source('BAT','J2',3.),source('DC5V','J1',2.)],stages=stages,extra_loads_a={'DC5V':1.36}),dict(name='charge_switch_off',condition='Switch off disconnects boost output; input budget includes full ILIM upper reserve rather than claiming measured charging current.',sources=[source('DC5V','J1',2.)],stages=[],load_scale=0,extra_loads_a={'DC5V':1.36})]
model=dict(circuit_sha256=dc_power_tree.circuit_hash(s),rails=rails,loads=loads,stage_references=['U4','U5','U6','U7'],storage_references=['L1'],scenarios=cases,reserves_a=reserves,programs=[dict(name='charger_input_limit',resistor_reference='R1',factor_min=1500.,factor_max=1720.,limit_a=1.5,reason='BQ24074 KILIM min/max, full resistor tolerance and declared50K TCR span'),dict(name='battery_charge_current',resistor_reference='R2',factor_min=797.,factor_max=975.,limit_a=.6,reason='BQ24074 KISET min/max, full resistor tolerance and declared50K TCR span')],resistor_temperature_span_c=50.,conditions='Declared engineering load/envelope/current screens only. No efficiency lower bound, simultaneous load characterization, thermal characterization, capacitor effective value, startup stability or battery sag is measured. Adapter-plus-battery case conservatively assigns all system demand to battery and full hardware input limit to adapter; it is not a physical current-sharing solution. Actual protected pack, effective MLCC capacitance and RF/acoustic operating behavior require further selection and bench validation.')
out=root/'cost-down';(out/'dc-model-proposal.json').write_text(json.dumps(model,indent=2))
with registry.using(root/'data'):
 result=dc_power_tree.evaluate(s,model,'uninstalled-engineering-proposal')
(out/'dc-reference-proposal.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['findings'],indent=2));print([(c['name'],[(x['net'],round(x['demand_a'],4)) for x in c['sources']]) for c in result['scenarios']])
