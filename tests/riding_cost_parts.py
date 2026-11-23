"""Source-backed equivalent parts and mask-defined ESP thermal land."""
import asyncio,json,hashlib,base64,re
from pathlib import Path
from riding_cost_common import ROOT,OUT,DATA,run
from pcb_mcp import footprints,backend
NAME='EXT_RIDING_COST'
VALUES={1300:'071K3L',1800:'071K8L',100000:'07100KL',10000:'0710KL',732000:'07732KL',20000:'0720KL',3300:'073K3L',47000:'0747KL'}
async def main(c):
 s=await c.state();byref={x['ref']:x for x in s['components'].values()};assets=[];parts=[];changes={}
 def asset(path,url,raw,kind='document'):
  assets.append(dict(path=path,kind=kind,source_url=url,sha256=hashlib.sha256(raw).hexdigest(),content_base64=base64.b64encode(raw).decode()))
 def part(old,mpn,maker,fid,fp,doc,facts,locator):
  ev=[dict(asset=doc,locator=locator,interpretation='Primary manufacturer dimensions and ratings; engineering land adaptation explicitly stated, physical qualification pending.')]
  pins=({str(i):f'Pin_{i}' for i in range(1,len(old['facts']['pin_mapping'])+1)} if old['kind']=='connector' else old['facts']['pin_mapping']);sid=old['symbol_id'];symhash=old['facts'].get('symbol_definition_hash')
  if not symhash:symhash=next(x for x in backend.catalog([sid.split(':')[0]]) if x['library_id']==sid)['definition_hash']
  p=dict(manufacturer=maker,mpn=mpn,kind=old['kind'],symbol_id=sid,footprint_id=fid,symbol_definition_hash=symhash,footprint_definition_hash=fp['definition_hash'],pin_mapping=pins,facts=facts,evidence={key:ev for key in (*facts,'identity','package','pin_mapping')});parts.append(p);return p
 for val,code in VALUES.items():
  mpn='RC0603FR-'+code;old=next(s['part_bindings'][x['id']] for x in byref.values() if x['ref'].startswith('R') and s['part_bindings'][x['id']]['facts'].get('value_ohm')==val and s['part_bindings'][x['id']]['facts'].get('tolerance_fraction')==.01)
  facts=dict(value_ohm=float(val),tolerance_fraction=.01,rated_power_w=.1,voltage_rating_v=75.,temperature_coefficient_ppm_per_k=100.)
  url='https://www.yageogroup.com/component-documentation/download/specsheet/'+mpn;doc='documents/'+mpn+'.json'
  extract=dict(mpn=mpn,source_url=url,page=1,read_date='2026-11-07',facts=facts,body_mm=[1.6,.8,.45],body_tolerance_mm=[.1,.1,.1],temperature_range_c=[-55,155],provenance='Facts transcribed from official generated PDF through web reader; raw download returned HTTP403. Not a full PDF copy.')
  raw=json.dumps(extract,indent=2).encode();(OUT/'sources'/(mpn+'.json')).write_bytes(raw);asset(doc,url,raw)
  fid=old['footprint_id'];fp=footprints.load(*footprints.entries(fid,library=fid.split(':')[0])[0]);p=part(old,mpn,'YAGEO',fid,fp,doc,facts,'Individual exact MPN specsheet page1: resistance, tolerance, power, TCR, voltage and 0603 dimensions')
  for ref,x in byref.items():
   if ref.startswith('R') and s['part_bindings'][x['id']]['facts'].get('value_ohm')==val and s['part_bindings'][x['id']]['facts'].get('tolerance_fraction')==.01:changes[ref]=p
 for count,ref in [(2,'J1'),(6,'J6')]:
  old=s['part_bindings'][byref[ref]['id']];source=Path(s['footprint_bindings'][byref[ref]['id']]['source']);name=f'XKB_1x{count:02d}_P2.54mm_Drill1.10mm';fid=NAME+':'+name
  raw=source.read_text().replace(source.stem,name);raw=re.sub(r'\(descr [^\n]*',f'(descr "XKB X6511WV-{count:02d}H-C60D30; 2.54mm pitch, 0.64mm square pins, 1.02mm manufacturer nominal hole enlarged to 1.10mm for assembly clearance; body 2.5mm wide")',raw)
  path=OUT/(NAME+'.pretty')/(name+'.kicad_mod');path.write_text(raw)
  url=json.loads((OUT/'sources/urls.json').read_text())[f'XKB-{count:02d}.pdf'];doc=f'documents/XKB-{count:02d}.pdf';asset(doc,url,(OUT/'sources'/f'XKB-{count:02d}.pdf').read_bytes());asset('footprints/'+NAME+'.pretty/'+path.name,url,path.read_bytes(),'footprint');fp=footprints.load(old['footprint_id'],source);fid=old['footprint_id']
  facts=dict(rated_current_a=3.,voltage_rating_v=250.,dimensions=dict(pins=count,lead_pitch_mm=2.54,recommended_drill_mm=1.02,adapted_drill_mm=1.1,body_length_mm=count*2.54,body_width_mm=2.5,pin_square_width_mm=.64,mating_length_mm=6.,tail_length_mm=3.),physical_sample_tested=False,package_validation='manufacturer_nominal_geometry_with_enlarged_drill')
  p=part(old,f'X6511WV-{count:02d}H-C60D30','XKB Connection',fid,fp,doc,facts,'Manufacturer drawing A0 page1, dimensions and electrical specifications')
  for r in (['J1','J2','J3','J4','J5'] if count==2 else ['J6','J7']):changes[r]=p
 old=s['part_bindings'][byref['U1']['id']];fid=NAME+':ESP32-S3-WROOM-1U_MaskGrid';path=OUT/(NAME+'.pretty')/'ESP32-S3-WROOM-1U_MaskGrid.kicad_mod';fp=footprints.load(fid,path)
 url=json.loads((OUT/'sources/urls.json').read_text())['esp32.pdf'];doc='documents/esp32.pdf';asset(doc,url,(OUT/'sources/esp32.pdf').read_bytes());asset('footprints/'+NAME+'.pretty/'+path.name,url,path.read_bytes(),'footprint')
 facts={k:v for k,v in old['facts'].items() if k not in ['pin_mapping','footprint_definition_hash','symbol_definition_hash','import_record']}
 changes['U1']=part(old,old['mpn'],old['manufacturer'],fid,fp,doc,facts,'v1.8 Figure11-2 page46: nine 0.9mm grid lands with 0.5mm gaps; adaptation retains original full copper, 12 thermal vias and paste area but masks copper gaps and holes')
 changes['U1']['footprint_variant_of']=old['definition_hash']
 package=dict(name=NAME,description='Cost-down parts retain electrical ratings and logical connectivity; mask-grid geometry adapted from manufacturer, no fabricated board claim',assets=assets,parts=parts)
 (OUT/'catalog-package.json').write_text(json.dumps(package,indent=2));print(await c.call('catalog_import',**package),flush=True)
 for ref,p in changes.items():
  hit=next(x for x in (await c.call('parts_search',query=p['mpn']))['candidates'] if x['mpn']==p['mpn'] and x['footprint_id']==p['footprint_id'])
  got=await c.call('part_get',candidate_id=hit['candidate_id'])
  fs=await c.call('footprint_search',query=p['footprint_id']);fp=next(x for x in fs['candidates'] if x['footprint_id']==p['footprint_id']);await c.call('footprint_get',candidate_id=fp['candidate_id'])
  args=dict(component_id=byref[ref]['id'],candidate_id=hit['candidate_id'],footprint_candidate_id=fp['candidate_id'])
  preview=await c.call('component_replacement_preview',project_id=c.pid,revision=c.rev,**args)
  assert preview['compatible'],preview
  result=await c.mutate('component_part_replace',**args,plan_sha256=preview['plan_sha256']);print(ref,p['mpn'],c.rev,flush=True)
 (OUT/'replacement-summary.json').write_text(json.dumps({r:dict(mpn=p['mpn'],footprint=p['footprint_id']) for r,p in changes.items()},indent=2))
if __name__=='__main__':asyncio.run(run(main,'parts.jsonl'))
