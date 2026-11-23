"""Complete prototype connectivity data plus resumable public-MCP application.

The declared external battery is a protected 1S 4.2V pack with a real 10k NTC;
it is not an unprotected cell. Connectors and off-board assembly are explicit.
This script does not assert successful physical or manufacturing validation.
"""
import asyncio,json,os,sys,uuid
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path('work/riding-intercom').resolve()
PID='e4569a2267ec4b0c8dbfe22e4bb60820'

def design():
    components={};nets={};notes={};groups={};nc={}
    def part(ref,mpn,group,value=None):
        components[ref]=dict(mpn=mpn,value=value);groups[ref]=group
    def net(name,*pins):nets.setdefault(name,[]).extend([list(p) for p in pins])
    def ic(ref,mpn,group,pins):
        part(ref,mpn,group)
        for name,numbers in pins.items():
            for number in numbers.split():net(name,(ref,number))
    counters={'C':0,'R':0}
    cap_parts={'100n':'C1608X7R1H104K080AA','1u':'C1608X5R1C105K080AA','10u':'C2012X5R1V106K125AC','120p':'06035A121JAT2A'}
    resistor_parts={'10k':'CRCW060310K0FKEA','100k':'CRCW0603100KFKEA','1.3k':'CRCW06031K30FKEA','1.8k':'CRCW06031K80FKEA','732k':'CRCW0603732KFKEA','47k':'CRCW060347K0FKEA','20k':'CRCW060320K0FKEA','4.3k':'CRCW06034K30FKEA','3.3k':'CRCW06033K30FKEA','453k':'TNPW0805453KBEEA','100k_p':'TNPW0805100KBEEA'}
    def passive(kind,value,a,b,group,reason):
        counters[kind]+=1;ref=f'{kind}{counters[kind]}'
        part(ref,(cap_parts if kind=='C' else resistor_parts)[value],group,'100k' if value=='100k_p' else value)
        net(a,(ref,'1'));net(b,(ref,'2'));notes[ref]=reason;return ref
    def cap(v,a,b,g,why):return passive('C',v,a,b,g,why)
    def res(v,a,b,g,why):return passive('R',v,a,b,g,why)
    ic('U1','ESP32-S3-WROOM-1U-N16R8','mcu',{'GND':'1 40 41','D3V3':'2','RESET':'3','I2C_SDA':'10','I2C_SCL':'11','I2S_MCLK':'9','I2S_BCLK':'17','I2S_LRCK':'26','I2S_ADC_DATA':'18','I2S_DAC_DATA':'12','PA_EN':'25','BOOT':'27','UART_RX':'36','UART_TX':'37','KEY_MUTE':'4','KEY_UP':'5','KEY_DOWN':'6'})
    ic('U2','ES7210','adc',{'GND':'1 2 8 21 33','A3V3':'6 7 22 23','I2C_SDA':'3','I2C_SCL':'4','I2S_MCLK':'5','I2S_BCLK':'9','I2S_LRCK':'10','I2S_ADC_DATA':'11','MIC1_N':'15','MIC1_P':'16','REFP12':'17','REFQ12':'18','MIC2_P':'19','MIC2_N':'20','MIC_BIAS':'24','REFQM':'25','BIAS34':'26','MIC4_N':'27','MIC4_P':'28','REFP34':'29','REFQ34':'30','AEC_P':'31','AEC_N':'32'})
    ic('U3','ES8311','dac',{'GND':'5 10 20 21','A3V3':'3 4 11','I2C_SCL':'1','I2S_MCLK':'2','I2S_BCLK':'6','I2S_LRCK':'8','I2S_DAC_DATA':'9','DAC_P':'12','DAC_N':'13','DAC_REF':'14','ADC_REF':'15','VMID':'16','DAC_MIC_N':'17','DAC_MIC_P':'18','I2C_SDA':'19'})
    ic('U4','BQ24074RGTR','charger',{'BAT':'2 3','GND':'4 6 8 17','VSYS':'5 10 11','DC5V':'13','BAT_NTC':'1','CHARGE_ILIM':'12','CHARGE_ISET':'16'})
    ic('U5','TPS61023DRLR','boost',{'BOOST_FB':'1','POWER_EN':'2','VSYS':'3','GND':'4','BOOST_SW':'5','V5':'6'})
    # Native module symbol stacks physical pads [1,2], [5,6,7], [9,10].
    ic('U6','TPSM82821ASILR','buck',{'V5':'1 3','D3V3':'5','GND':'9','BUCK_FB':'8'})
    ic('U7','TLV75533PDBVR','audio_power',{'V5':'1 3','GND':'2','A3V3':'5'})
    ic('U8','PAM8302AADCR','pa',{'PA_EN':'1','PA_IN_P':'3','PA_IN_N':'4','SPK_P':'5','V5':'6','GND':'7','SPK_N':'8'})
    for ref,group,out in [('MK1','mic1','MIC1_OUT'),('MK2','mic2','MIC2_OUT')]:
        ic(ref,'MSM381A3729H9BPC',group,{'A3V3':'1','GND':'2 3',out:'4'})
        cap('100n','A3V3','GND',group,'Local microphone supply bypass; dedicated quiet3.3V rail within manufacturer1.5-3.6V range; output AC-coupled to ADC')
    part('L1','74438357010','boost');net('VSYS',('L1','1'));net('BOOST_SW',('L1','2'))
    ports={
      'J1':('charger',['DC5V','GND'],'5V regulated DC input 2A; keyed/polarized enclosure harness, not USB host input'),
      'J2':('charger',['BAT','GND'],'Protected 1S LiPo, 4.2V full, pack discharge >=3A; pack protection required'),
      'J3':('charger',['BAT_NTC','GND'],'Real 10k NTC attached to battery; no fixed resistor substitute'),
      'J4':('boost',['VSYS','POWER_EN'],'External latching power switch; low-current EN control'),
      'J5':('pa',['SPK_P','SPK_N'],'4ohm >=3W speaker, short twisted pair; neither terminal is GND'),
      'J6':('mcu',['GND','UART_TX','UART_RX','RESET','BOOT','D3V3'],'3.3V UART programming; TX/RX board directions, last pin voltage sense, no 5V injection'),
      'J7':('keys',['GND','KEY_MUTE','GND','KEY_UP','GND','KEY_DOWN'],'Three normally-open momentary enclosure buttons; short each key to adjacent ground'),
    }
    for ref,(group,signals,why) in ports.items():
        part(ref,'61300211121' if len(signals)==2 else '61300611121',group);notes[ref]=why
        for i,n in enumerate(signals):net(n,(ref,str(i+1)))
    res('1.3k','CHARGE_ILIM','GND','charger','KILIM1500..1720 / R1%=1.143..1.336A, adapter>=2A')
    res('1.8k','CHARGE_ISET','GND','charger','KISET797..975 / R1%=0.438..0.547A, nominal0.494A; thermal regulation may reduce')
    for rail in ['DC5V','BAT','VSYS']:cap('10u',rail,'GND','charger','BQ24074 input/battery/output bypass, within source recommended range')
    res('100k','POWER_EN','GND','boost','Default off; open external switch disables boost')
    res('732k','V5','BOOST_FB','boost','PWM VREFtyp=.595V gives4.9504V; PFM VREFtyp=.601V gives5.00032V; nominal equation is not a guaranteed envelope')
    res('100k','BOOST_FB','GND','boost','Feedback bottom <300k per TPS61023')
    cap('10u','VSYS','GND','boost','Local boost input bypass')
    for _ in range(3):cap('10u','V5','GND','boost','Parallel boost output ceramic; evaluate effective capacitance at5V')
    res('453k','D3V3','BUCK_FB','buck','0.6*(1+453k/100k)=3.318V nominal; precision divider')
    res('100k_p','BUCK_FB','GND','buck','Precision divider bottom 100k')
    cap('120p','D3V3','BUCK_FB','buck','Cff=12us/100k, TI forced PWM compensation')
    cap('10u','V5','GND','buck','Local buck input bypass')
    cap('10u','D3V3','GND','buck','Local buck output bypass')
    cap('10u','D3V3','GND','mcu','ESP32 local bulk')
    cap('100n','D3V3','GND','mcu','ESP32 local high-frequency bypass')
    res('10k','D3V3','RESET','mcu','ESP32 EN pull-up, datasheet reset RC')
    cap('1u','RESET','GND','mcu','ESP32 EN reset RC nominal10ms')
    res('10k','D3V3','BOOT','mcu','Default normal boot; J6 can ground IO0 for download')
    for key in ['KEY_MUTE','KEY_UP','KEY_DOWN']:res('10k','D3V3',key,'keys','External button pull-up')
    for n in ['I2C_SDA','I2C_SCL']:res('4.3k','A3V3',n,'adc','External I2C pull-up;100kHz bus')
    cap('1u','V5','GND','audio_power','TLV755 local input')
    cap('10u','A3V3','GND','audio_power','TLV755 local output; analog rail separated from digital buck')
    for _ in range(4):cap('100n','A3V3','GND','adc','One local ES7210 supply bypass per supply pin')
    cap('1u','A3V3','GND','adc','ADC local bulk')
    for n in ['REFP12','REFQ12','REFQM','REFP34','REFQ34','MIC_BIAS','BIAS34']:
        cap('1u',n,'GND','adc','ES7210 reference/bias capacitor per Korvo2 reference')
    for i in [1,2]:
        cap('1u',f'MIC{i}_OUT',f'MIC{i}_P','adc','AC-coupled microphone output')
        cap('1u','GND',f'MIC{i}_N','adc','AC ground negative input; bias established inside ADC')
    for n in ['MIC4_P','MIC4_N']:cap('1u',n,'GND','adc','Unused fourth capture channel AC grounded, firmware retains four-slot frame')
    for _ in range(3):cap('100n','A3V3','GND','dac','One local DAC supply bypass per supply pin')
    cap('1u','A3V3','GND','dac','DAC analog local bulk')
    for n in ['VMID','DAC_REF','ADC_REF','DAC_MIC_P','DAC_MIC_N']:
        cap('1u',n,'GND','dac','Reference/unused ADC input AC bypass; DAC-only operation')
    for polarity in ['P','N']:
        cap('1u','DAC_'+polarity,'DAC_AC_'+polarity,'dac','Remove DAC output common-mode voltage')
        res('47k','DAC_AC_'+polarity,'PA_IN_'+polarity,'pa','PAM gain150k/(10k+47k)=2.63V/V, initial prototype gain')
        res('20k','DAC_AC_'+polarity,'AEC_DIV_'+polarity,'aec','Symmetric DAC loopback attenuation, not class-D switch-node sampling')
        res('3.3k','AEC_DIV_'+polarity,'GND','aec','3.3/(20+3.3)=0.1416 nominal attenuation')
        cap('1u','AEC_DIV_'+polarity,'AEC_'+polarity,'aec','ADC3 input common-mode isolation, ADC channel gain0dB')
    res('10k','PA_EN','GND','pa','Hardware default mute until firmware initializes codecs and volume')
    cap('1u','V5','GND','pa','PAM local high-frequency decoupling')
    cap('10u','V5','GND','pa','PAM local bulk decoupling')
    for _ in range(2):cap('120p','V5','BOOST_FB','boost','Two120pC0G in parallel:240p feedforward across732k upper feedback; TI TPS61023 eq10 gives906Hz zero, near1kHz recommendation forlarge output capacitance')
    nc.update({'U2':{'12':'SDOUT2/TDMIN unused in single-ADC master/TDM output mode','13':'Interrupt output not used','14':'Digital microphone clock unused with analog microphones'},
      'U3':{'7':'ADC serial output unused in DAC-only operation'},
      'U4':{'7':'Open-drain power-good indicator unused','9':'Open-drain charge indicator unused','14':'Leave floating for default safety timers per datasheet','15':'Leave floating for default10% charge termination per datasheet'},
      'U6':{'4':'Unused PG open-drain; manufacturer permits floating'},'U7':{'4':'Manufacturer NC'},'U8':{'2':'Manufacturer NC'}})
    used={str(p) for pins in nets.values() for r,p in pins if r=='U1'}
    nc['U1']={str(i):('Reserved by N16R8 internal Octal PSRAM; no external connection' if i in [28,29,30] else 'Unused module GPIO; boot strapping internal defaults retained') for i in range(1,42) if str(i) not in used}
    return dict(components=components,nets=nets,no_connect=nc,groups=groups,notes=notes,
      external_assembly=ports,qualification='Prototype connectivity design; requires ERC, source circuit review and PCB layout checks before release')

async def main():
    spec=design();out=ROOT/'hardware-circuit';out.mkdir(exist_ok=True)
    (out/'design.json').write_text(json.dumps(spec,indent=2,ensure_ascii=False))
    log=out/'transcript.jsonl'
    parameters=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
    async with stdio_client(parameters) as (r,w),ClientSession(r,w) as s:
        await s.initialize()
        async def call(tool,args):
            reply=await s.call_tool(tool,{'request':args},read_timeout_seconds=timedelta(seconds=300))
            with log.open('a') as f:f.write(json.dumps(dict(tool=tool,request=args,response=reply.model_dump(mode='json')),ensure_ascii=False)+'\n')
            if reply.isError:raise RuntimeError(str(reply))
            return reply.structuredContent['data']
        state=await call('project_read',dict(project_id=PID));rev=state['revision']
        async def mutate(tool,**args):
            nonlocal rev
            result=await call(tool,dict(project_id=PID,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=result['revision'];return result
        components={c['ref']:c for c in state['components'].values()};cache={}
        for ref,row in spec['components'].items():
            old=components.get(ref)
            if old and old['id'] in state['footprint_bindings'] and state['part_bindings'].get(old['id'],{}).get('mpn')==row['mpn']:
                if row['value'] is not None and old['value']!=row['value']:raise ValueError('Existing value changed: '+ref)
                continue
            if row['mpn'] not in cache:
                hits=(await call('parts_search',dict(query=row['mpn'])))['candidates']
                part=await call('part_get',dict(candidate_id=next(p['candidate_id'] for p in hits if p['mpn']==row['mpn'])))
                sym=next(p for p in (await call('library_search',dict(query=part['symbol_id'])))['candidates'] if p['library_id']==part['symbol_id'])
                sym=await call('library_get',dict(candidate_id=sym['candidate_id']))
                fp=next(p for p in (await call('footprint_search',dict(query=part['footprint_id'],library=part['footprint_id'].split(':')[0])))['candidates'] if p['footprint_id']==part['footprint_id'])
                fp=await call('footprint_get',dict(candidate_id=fp['candidate_id']));cache[row['mpn']]=(part,sym,fp)
            part,sym,fp=cache[row['mpn']]
            if ref not in components:
                added=await mutate('component_add',candidate_id=sym['candidate_id'],ref=ref,value=row['value'] or sym['symbol'])
                components[ref]=dict(id=added['component_id'],pins=added['pins'])
            c=components[ref]
            if c['id'] not in state['footprint_bindings']:await mutate('footprint_bind',component_id=c['id'],candidate_id=fp['candidate_id'],reason='Manufacturer pin/package checked; actual layout and assembly review required')
            if c['id'] not in state['part_bindings']:await mutate('part_bind',component_id=c['id'],candidate_id=part['candidate_id'])
            print('Bound',ref,row['mpn'],flush=True)
        def pin(ref,number):
            c=components[ref];p=next(p for p in c['pins'] if str(number) in p.get('physical_numbers',[p['number']]))
            return dict(component_id=c['id'],pin_id=p['pin_id'])
        existing={n['name']:n for n in state['nets'].values()}
        # Clear only the project's current members before a full atomic-per-net
        # replacement. Intermediate states remain drafts and cannot be exported.
        for n in existing.values():
            if n['pins']:await mutate('net_members_set',net_id=n['id'],pins=[])
        net_ids={}
        for name,pins in spec['nets'].items():
            n=existing.get(name)
            if n is None:
                result=await mutate('net_create',name=name,kind='ground' if name=='GND' else 'power' if name in ['D3V3','A3V3','DC5V','BAT','VSYS','V5','MIC_BIAS','BIAS34'] else 'signal');nid=result['net_id']
            else:nid=n['id']
            net_ids[name]=nid
            members=list({(p['component_id'],p['pin_id']):p for p in [pin(*p) for p in pins]}.values())
            await mutate('net_members_set',net_id=nid,pins=members)
        for ref,pins in spec['no_connect'].items():
            for number,reason in pins.items():await mutate('pin_intent_set',pin=pin(ref,number),intent='no_connect',reason=reason)
        # Source declarations are only real external connectors, never internal
        # fabricated PWR_FLAGs to hide a missing regulator output.
        for ref,name,voltage in [('J1','DC5V',5.),('J2','BAT',3.7)]:
            old=next((p for p in state['power_sources'].values() if p['boundary_pin']==pin(ref,1)),None)
            await mutate('power_source_set',source_id=old['id'] if old else None,net_id=net_ids[name],ground_net_id=net_ids['GND'],boundary_pin=pin(ref,1),nominal_v=voltage,reason=spec['notes'][ref])
        state=await call('project_read',dict(project_id=PID));(out/'state.json').write_text(json.dumps(state,indent=2,ensure_ascii=False))
        result=await call('spec_validate',dict(project_id=PID));(out/'validation.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
        print('Circuit applied',len(components),'components',len(spec['nets']),'nets, revision',rev,'verdict',result['verdict'],flush=True)

if __name__=='__main__':asyncio.run(main())
