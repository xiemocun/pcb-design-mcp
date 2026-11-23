"""Import source-checked prototype ICs through the public MCP, no core changes.

Data adaptation is explicit: new symbols from manufacturer pin tables, existing
KiCad land patterns with matching pitch/body/EP dimensions. This is NOT a complete
circuit, manufacturing approval, RF simulation or acoustic qualification.
"""
import asyncio, base64, hashlib, json, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp import backend, footprints

ROOT = Path('work/riding-intercom').resolve()
OUT = ROOT / 'parts-01'
NAME = 'EXT_RIDING_AUDIO'
SOURCES = {
    'esp32-s3-wroom.pdf': 'https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf',
    'es7210.pdf': 'https://files.waveshare.com/wiki/common/ES7210_DS.pdf',
    'es8311.pdf': 'https://dl.espressif.com/dl/schematics/Audio_ES8311.pdf',
    'korvo2-reference.pdf': 'https://dl.espressif.com/dl/schematics/SCH_ESP32-S3-Korvo-2_V3.1.2_20240116.pdf',
}
PARTS = [
    dict(mpn='ESP32-S3-WROOM-1U-N16R8', manufacturer='Espressif Systems', kind='mcu',
         doc='esp32-s3-wroom.pdf', locator='Tables 1-1 and 3-1, Table 6-2, section 10 module land pattern',
         fp='RF_Module:ESP32-S3-WROOM-1U', supply=['3V3'], minimum=3.0,
         pins='GND 3V3 EN IO4 IO5 IO6 IO7 IO15 IO16 IO17 IO18 IO8 IO19 IO20 IO3 IO46 IO9 IO10 IO11 IO12 IO13 IO14 IO21 IO47 IO48 IO45 IO0 IO35 IO36 IO37 IO38 IO39 IO40 IO41 IO42 RXD0 TXD0 IO2 IO1 GND EPAD'.split()),
    dict(mpn='ES7210', manufacturer='Everest Semiconductor', kind='audio_adc',
         doc='es7210.pdf', locator='Rev21.0 page4 pinout, page9 recommended conditions, page26 package; reference board page4 EP33',
         fp='Package_DFN_QFN:VQFN-32-1EP_4x4mm_P0.4mm_EP2.8x2.8mm',
         supply=['VDDP','VDDD','VDDA','VDDM'], minimum=1.6,
         pins='AD0 AD1 CDATA CCLK MCLK VDDP VDDD GNDD SCLK LRCK SDOUT1/TDMOUT SDOUT2/TDMIN INT DMIC_CLK MIC1N MIC1P REFP12 REFQ12 MIC2P MIC2N GNDA VDDA VDDM MICBIAS12 REFQM MICBIAS34 MIC4N MIC4P REFP34 REFQ34 MIC3P MIC3N EPAD'.split()),
    dict(mpn='ES8311', manufacturer='Everest Semiconductor', kind='audio_codec',
         doc='es8311.pdf', locator='Rev7.0 page2 pinout, page7 recommended conditions, page11 package; reference board page4 EP21',
         fp='Package_DFN_QFN:VQFN-20-1EP_3x3mm_P0.4mm_EP1.7x1.7mm',
         supply=['PVDD','DVDD','AVDD'], minimum=1.7,
         pins='CCLK MCLK PVDD DVDD DGND SCLK/DMIC_SCL ASDOUT LRCK DSDIN AGND AVDD OUTP OUTN DACVREF ADCVREF VMID MIC1N MIC1P/DMIC_SDA CDATA CE EPAD'.split()),
]

def write(path, value): path.write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
def symbol(p):
    name = p['mpn']; n = len(p['pins']); half = (n+1)//2; height = (half+1)*2.54
    rows = [f'(symbol "{name}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)',
            f'(property "Reference" "U" (at 0 {height/2+2.54} 0) (effects (font (size 1.27 1.27))))',
            f'(property "Value" "{name}" (at 0 {-height/2-2.54} 0) (effects (font (size 1.27 1.27))))',
            f'(property "Footprint" "{p["fp"]}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))',
            f'(symbol "{name}_0_1" (rectangle (start -12.7 {height/2}) (end 12.7 {-height/2}) (stroke (width 0.254) (type default)) (fill (type background))))',
            f'(symbol "{name}_1_1"']
    for i, pin in enumerate(p['pins']):
        electrical = 'bidirectional'
        if pin in p['supply'] or pin in ('GND','GNDD','GNDA','DGND','AGND','EPAD'): electrical='power_in'
        elif pin in ('EN','AD0','AD1','CE','CCLK','MCLK','DSDIN','MIC1N','MIC1P','MIC2N','MIC2P','MIC3N','MIC3P','MIC4N','MIC4P'): electrical='input'
        elif pin in ('ASDOUT','OUTP','OUTN','INT','DMIC_CLK','SDOUT1/TDMOUT'): electrical='output'
        elif pin.startswith(('REF','MICBIAS')) or pin in ('DACVREF','ADCVREF','VMID'): electrical='passive'
        x,angle=(-15.24,0) if i<half else (15.24,180)
        y=height/2-2.54*(i%half+1)
        rows.append(f'(pin {electrical} line (at {x} {y} {angle}) (length 2.54) (name "{pin}" (effects (font (size 1 1)))) (number "{i+1}" (effects (font (size 1 1)))))')
    return '\n'.join(rows)+'))'

async def main():
    OUT.mkdir(exist_ok=False)
    lib=OUT/(NAME+'.kicad_sym')
    lib.write_text('(kicad_symbol_lib (version 20250114) (generator "source_adaptation")\n'+'\n'.join(symbol(p) for p in PARTS)+')\n')
    stat=lib.stat(); symbols={s['symbol']:s for s in backend._catalog_library(str(lib),stat.st_mtime_ns,stat.st_size)}
    assets=[]
    def asset(path, kind, source, data):
        assets.append(dict(path=path,kind=kind,source_url=source,sha256=hashlib.sha256(data).hexdigest(),content_base64=base64.b64encode(data).decode()))
    for doc,url in SOURCES.items(): asset('documents/'+doc,'document',url,(ROOT/'sources'/doc).read_bytes())
    asset('symbols/'+lib.name,'symbol',SOURCES['korvo2-reference.pdf'],lib.read_bytes())
    parts=[]
    for p in PARTS:
        fp=footprints.load(*footprints.entries(p['fp'],library=p['fp'].split(':')[0])[0])
        facts=dict(power_budget_model='supply_pin_load',supply_pins=p['supply'],supply_min_v=p['minimum'],supply_max_v=3.6)
        evidence={k:[dict(asset='documents/'+p['doc'],locator=p['locator'],interpretation='Manufacturer identity, pin table and recommended voltage. KiCad land pattern matches documented pitch/body/EP; assembly fit remains to be checked. Supply load is a declared-load abstraction, not an audio/RF model.')] for k in (*facts,'identity','package','pin_mapping')}
        if p['mpn']!='ESP32-S3-WROOM-1U-N16R8':
            evidence['pin_mapping'].append(dict(asset='documents/korvo2-reference.pdf',locator='Sheet4 audio, U28 ES7210 / U11 ES8311',interpretation='Exposed grounding pad uses physical number33 /21 respectively.'))
        parts.append(dict(manufacturer=p['manufacturer'],mpn=p['mpn'],kind=p['kind'],symbol_id=NAME+':'+p['mpn'],footprint_id=p['fp'],symbol_definition_hash=symbols[p['mpn']]['definition_hash'],footprint_definition_hash=fp['definition_hash'],pin_mapping={str(i+1):n for i,n in enumerate(p['pins'])},facts=facts,evidence=evidence))
    package=dict(name=NAME,description='Source-adapted riding prototype IC symbols; identity and pins only, not a complete qualified circuit.',assets=assets,parts=parts)
    write(OUT/'catalog-package.json',package)
    transcript=[]
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
    async with stdio_client(server) as (r,w),ClientSession(r,w) as s:
        await s.initialize()
        async def call(name,args):
            result=await s.call_tool(name,{'request':args})
            transcript.append(dict(tool=name,arguments='retained catalog-package.json' if name=='catalog_import' else args,response=result.model_dump(mode='json')))
            write(OUT/'transcript.json',transcript)
            assert not result.isError,str(result)
            return result.structuredContent['data']
        imported=await call('catalog_import',package)
        found=[]
        for p in PARTS:
            hits=(await call('parts_search',dict(query=p['mpn'])))['candidates']
            part=await call('part_get',dict(candidate_id=next(h['candidate_id'] for h in hits if h['mpn']==p['mpn'])))
            found.append(part)
        write(OUT/'result.json',dict(imported=imported,parts=found,schematic_complete=False,physical_verified=False))
        print('Imported source-linked prototype ICs:', ', '.join(p['mpn'] for p in PARTS))

if __name__=='__main__': asyncio.run(main())
