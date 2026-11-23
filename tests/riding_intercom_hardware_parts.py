"""Source adapters for the riding prototype, executed via public MCP import.

Manufacturer tables, not package-name guesses, define identities. A successful
import is only a catalog check; it does not qualify a circuit or layout.
"""
import asyncio, base64, hashlib, json, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp import backend, footprints
from riding_intercom_parts import symbol

ROOT=Path('work/riding-intercom').resolve()
NAME='EXT_RIDING_POWER'

def build_package(out):
    assets=[]
    def asset(name,kind,url,data):
        assets.append(dict(path=name,kind=kind,source_url=url,sha256=hashlib.sha256(data).hexdigest(),content_base64=base64.b64encode(data).decode()))
    docs={
      'bq24074.pdf':('https://www.ti.com/lit/ds/symlink/bq24074.pdf',ROOT/'sources/bq24074.pdf'),
      'tps61023.pdf':('https://www.ti.com/lit/ds/symlink/tps61023.pdf',ROOT/'sources/tps61023.pdf'),
      'pam8302a.pdf':('https://www.diodes.com/datasheet/download/PAM8302A.pdf',ROOT/'sources/pam8302a.pdf'),
      'msm381.pdf':('https://uploadcdn.oneyac.com/attachments/files/brand_pdf/memsensing/9B/12/MEMSensing-MSM381A3729H9BPC.pdf',ROOT/'sources/msm381.pdf'),
      'crcw.pdf':('https://www.vishay.com/docs/20035/dcrcwe3.pdf',Path('evidence-cache/vishay-dcrcwe3-2026.pdf')),
    }
    for doc,(url,path) in docs.items():asset('documents/'+doc,'document',url,path.read_bytes())
    # The manufacturer PDF is readable by the web tool but raw download returns
    # HTTP403. Retain the small, explicitly transcribed manufacturer table instead.
    url='https://www.we-online.com/components/products/datasheet/74438357010.pdf'
    extract=dict(source_url=url,revision='003.002',date='2026-09-01',page=1,
      mpn='74438357010',body_mm=[4.1,4.1,3.1],inductance_h=1e-6,tolerance_fraction=.2,
      dcr_max_ohm=.0135,saturation_10percent_typ_a=6.2,
      land_pattern_1=dict(outer_x=3.35,inner_x=1.39,height_y=3.7),
      restriction='No vias and traces underneath the inductor',
      provenance='Transcribed from primary PDF through web open; raw curl403 retained in session. Typical saturation is not a guaranteed limit.')
    data=json.dumps(extract,indent=2).encode();(out/'inductor-source.json').write_bytes(data)
    asset('documents/inductor-source.json','document',url,data)
    custom=[dict(mpn='TPS61023DRLR',fp='Package_TO_SOT_SMD:SOT-563',supply=['VIN'],pins=['FB','EN','VIN','GND','SW','VOUT']),
            dict(mpn='MSM381A3729H9BPC',fp=NAME+':MSM381A3729H9BPC',supply=['VDD'],pins=['VDD','GND','GND','OUT'])]
    raw='(kicad_symbol_lib (version 20250114) (generator "source_adaptation")\n'+'\n'.join(symbol(p) for p in custom)+')\n'
    # Converter output is an actual power source to downstream power-input pins.
    lib=out/(NAME+'.kicad_sym');lib.write_text(raw)
    stat=lib.stat();symbols={s['library_id']:s for s in backend._catalog_library(str(lib),stat.st_mtime_ns,stat.st_size)}
    # Give SW and VOUT the correct types by parsing/replacing the relevant pin
    # blocks, keeping the source pin numbering unchanged.
    import re
    raw=re.sub(r'\(pin bidirectional(?= line[^\n]*\(name "VOUT")','(pin power_out',raw)
    lib.write_text(raw);stat=lib.stat();symbols={s['library_id']:s for s in backend._catalog_library(str(lib),stat.st_mtime_ns,stat.st_size)}
    asset('symbols/'+lib.name,'symbol',docs['tps61023.pdf'][0],lib.read_bytes())
    # Datasheet page6 is a BOTTOM view. Mirror X for the top-view PCB land pattern.
    # Top acoustic port: no drilled sound hole through this PCB.
    fpname='MSM381A3729H9BPC'
    rows=[f'(footprint "{fpname}" (version 20241229) (generator "source_adaptation") (layer "F.Cu")',
          '(descr "MSM381 top-port microphone; page6 bottom view mirrored; 0.8mm lands")',
          '(attr smd)', '(fp_rect (start -1.475 -1.88) (end 1.475 1.88) (stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))',
          '(fp_rect (start -1.725 -2.13) (end 1.725 2.13) (stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))',
          '(fp_circle (center 1.7 -1.5) (end 1.8 -1.5) (stroke (width 0.15) (type default)) (fill none) (layer "F.SilkS"))']
    for n,x,y in [(1,.85,-1.23),(2,.85,1.23),(3,-.85,1.23),(4,-.85,-1.23)]:
        rows.append(f'(pad "{n}" smd circle (at {x} {y}) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))')
    file=out/(fpname+'.kicad_mod');file.write_text('\n'.join(rows)+')\n')
    asset('footprints/'+NAME+'.pretty/'+file.name,'footprint',docs['msm381.pdf'][0],file.read_bytes())
    specs=[
      ('Texas Instruments','BQ24074RGTR','charger','Battery_Management:BQ24074RGT','Package_DFN_QFN:VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm','bq24074.pdf','Table7-1, Fig7-2, application and package drawings',{}),
      ('Texas Instruments','TPS61023DRLR','boost_converter',NAME+':TPS61023DRLR','Package_TO_SOT_SMD:SOT-563','tps61023.pdf','Section5 pin functions; DRL package and section8 reference circuit',{}),
      ('Diodes Incorporated','PAM8302AADCR','audio_amplifier','Amplifier_Audio:PAM8302AAD','Package_SO:SOIC-8_3.9x4.9mm_P1.27mm','pam8302a.pdf','Pages1/2/3/7/9 SO-8 pinout, supply, gain and ordering',dict(power_budget_model='supply_pin_load',supply_pins=['VDD'],ground_pins=['GND'],supply_min_v=2.,supply_max_v=5.5)),
      ('MEMSensing','MSM381A3729H9BPC','microphone',NAME+':MSM381A3729H9BPC',NAME+':MSM381A3729H9BPC','msm381.pdf','Pages2 and6: supply1.5-3.6V, top port, numbered bottom pads mirrored for PCB top view',dict(power_budget_model='supply_pin_load',supply_pins=['VDD'],ground_pins=['GND'],supply_min_v=1.5,supply_max_v=3.6)),
      ('Wurth Elektronik','74438357010','inductor','Device:L','Inductor_SMD:L_Wuerth_MAPI-4030','inductor-source.json','Page1 primary manufacturer table, transcribed extract',dict(inductance_h=1e-6,tolerance_fraction=.2,dcr_max_ohm=.0135)),
    ]
    for value,code in [(1300,'1K30'),(1800,'1K80'),(732000,'732K'),(47000,'47K0'),(1000,'1K00'),(2200,'2K20'),(20000,'20K0'),(4300,'4K30'),(3300,'3K30')]:
        specs.append(('Vishay','CRCW0603'+code+'FKEA','resistor','Device:R','Resistor_SMD:R_0603_1608Metric','crcw.pdf','Ordering table: CRCW0603, resistance code, F=1%, K=100ppm/K, EA; ratings and dimensions',dict(value_ohm=float(value),tolerance_fraction=.01,rated_power_w=.1,voltage_rating_v=75.,temperature_coefficient_ppm_per_k=100.)))
    parts=[]
    for manufacturer,mpn,kind,sid,fid,doc,locator,facts in specs:
        if sid not in symbols:symbols.update({s['library_id']:s for s in backend.catalog([sid.split(':')[0]])})
        s=symbols[sid]
        fp=footprints.load(fid,file) if fid.startswith(NAME+':') else footprints.load(*footprints.entries(fid,library=fid.split(':')[0])[0])
        ev=[dict(asset='documents/'+doc,locator=locator,interpretation='Source identity, pin/package and stated table facts only; circuit/layout qualification is separate.')]
        parts.append(dict(manufacturer=manufacturer,mpn=mpn,kind=kind,symbol_id=sid,footprint_id=fid,symbol_definition_hash=s['definition_hash'],footprint_definition_hash=fp['definition_hash'],pin_mapping={n:p['name'] or 'passive' for p in s['pins'] for n in p['physical_numbers']},facts=facts,evidence={key:ev for key in (*facts,'identity','package','pin_mapping')}))
    return dict(name=NAME,description='Manufacturer-bound power/audio prototype parts; pin and package adaptation, not circuit qualification',assets=assets,parts=parts)

async def main():
    number=1
    while (ROOT/f'hardware-parts-{number:02d}').exists():number+=1
    out=ROOT/f'hardware-parts-{number:02d}';out.mkdir()
    package=build_package(out);(out/'package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2))
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
    async with stdio_client(server) as (r,w),ClientSession(r,w) as s:
        await s.initialize();reply=await s.call_tool('catalog_import',{'request':package})
        (out/'import-result.json').write_text(reply.model_dump_json(indent=2))
        assert not reply.isError,str(reply)
        print('Imported',len(package['parts']),'source-bound hardware parts')

if __name__=='__main__':asyncio.run(main())
