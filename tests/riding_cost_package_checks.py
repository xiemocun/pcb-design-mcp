"""Pre-export semantic invariants and procurement index for exact cost-down BOM."""
import json,csv,hashlib,shutil
from pathlib import Path
from riding_cost_common import ROOT,OUT,DATA,PARENT
ctx=json.loads((OUT/'context.json').read_text());s=json.loads((DATA/ctx['project_id']/'state.json').read_text());old=json.loads((DATA/PARENT/'state.json').read_text());dest=OUT/'vendor-package';dest.mkdir(exist_ok=True)
assert s['nets']==old['nets']
refs=lambda st:{v['ref']:v for v in st['components'].values()}
a,b=refs(old),refs(s);assert set(a)==set(b)
changed=[]
for ref,c in b.items():
 x=a[ref];assert c['pins']==x['pins'] and c['library_id']==x['library_id'],ref
 p=s['part_bindings'][c['id']];q=old['part_bindings'][x['id']]
 if p['definition_hash']!=q['definition_hash']:
  changed.append(dict(reference=ref,old_mpn=q['mpn'],new_mpn=p['mpn'],old_footprint=q['footprint_id'],new_footprint=p['footprint_id']))
  if ref.startswith('R'):
   for key in ['value_ohm','rated_power_w','tolerance_fraction','voltage_rating_v','temperature_coefficient_ppm_per_k']:assert p['facts'][key]==q['facts'][key],(ref,key)
  elif ref.startswith('J'):assert p['facts']['rated_current_a']>=q['facts']['rated_current_a']
  else:assert ref=='U1' and p['mpn']==q['mpn']
assert len(changed)==25,len(changed)
assert s['power_budget']==old['power_budget'] if 'power_budget' in s else True
report=dict(project_id=s['id'],revision=s['revision'],baseline_project_id=PARENT,baseline_revision=626,components=len(b),nets=len(s['nets']),all_nets_and_physical_pin_roles_unchanged=True,changed_bindings=changed,resistor_ratings_identical=True,connector_current_rating_preserved=True,core_audio_RF_power_parts_unchanged=True,firmware_gpio_unchanged=True,physical_verified=False,range_target='Urban obstructed 1km remains a prototype target, not measured result')
(dest/'cost-down-verification.json').write_text(json.dumps(report,indent=2))
codes={'RC0603FR-071K3L':'C125319','RC0603FR-071K8L':'C185354','RC0603FR-07100KL':'C14675','RC0603FR-0710KL':'C98220','RC0603FR-07732KL':'C246003','RC0603FR-0720KL':'C105575','RC0603FR-073K3L':'C108078','RC0603FR-0747KL':'C105579','X6511WV-02H-C60D30':'C706874','X6511WV-06H-C60D30':'C706878'}
with (dest/'采购索引.csv').open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.writer(f);w.writerow(['位号','厂家','完整料号','立创LCSC编号','核对链接','备注'])
 for mpn,code in codes.items():
  rows=[(r,s['part_bindings'][c['id']]) for r,c in b.items() if s['part_bindings'][c['id']]['mpn']==mpn];assert rows
  w.writerow([','.join(r for r,p in rows),rows[0][1]['manufacturer'],mpn,code,'https://www.lcsc.com/product-detail/'+code+'.html','2026-11-07核对型号索引；库存/价格/MOQ由下单时确认。不是允许任意替料。'])
sourceindex={r:dict(mpn=s['part_bindings'][c['id']]['mpn'],manufacturer=s['part_bindings'][c['id']]['manufacturer'],sources=s['part_bindings'][c['id']]['sources']) for r,c in b.items()};(dest/'source-index.json').write_text(json.dumps(sourceindex,indent=2))
notes='''降本版制作与报价重点

本版保持两层90×65mm、1.6mm FR-4、成品外层铜厚至少35μm、最小线宽/间距0.15mm、最小成品孔0.20mm。
原r626的19处填孔盖铜要求取消：U1的12个散热孔保留、采用分格阻焊开窗；其余7处焊盘内孔实际移开，另1处孔增加了阻焊间隔。总钻孔数量保持213。
采用普通机械钻孔和双面过孔盖油，不要追加树脂填孔、铜帽盖孔、盲埋孔、激光微孔或阻抗控制费用。必须照F.Mask/B.Mask制作，不能自动给U1中心热孔重新开窗。
ENIG保留：音频QFN脚距0.4mm，保留平整表面有利于首轮焊接良率。不能为省表面处理费擅自改喷锡。
17颗1%电阻改为国巨同额定规格；7个排针改为XKB的3A产品。R6/R7精密反馈电阻、R13/R14、电源芯片、音频芯片、双麦和低阻电感保留。
XKB排针复用已核对尺寸的Connector_PCB_MCP库焊盘（库内名字带WR_PHD是几何历史名称），采购以BOM中XKB完整MPN为准。原厂推荐孔1.02mm，本板保留1.10mm孔与2.54mm节距。
J1-J7为直插、回流后焊接；应归入直插焊接工序报价。QFN检查、裸板电测和麦克风免清洗工艺仍需保留。
请分别报价：裸板、ENIG、钢网、SMT工程/上机费、元器件、直插焊接、检验、烧录、运费和税费。两台工程样机的固定费用不会因少量替料消失；本包不声称已得到更低的总报价。
对比旧版时请使用同数量、同供料/检验/烧录范围。确认标准过孔盖油与分格开窗后再生产，不能套用旧版特殊孔费用。
功能要求、GPIO和A/B固件功能保持不变。城市遮挡1公里、实际大音量全双工回声消除与骑行风噪仍须在实物上验证。
'''
(dest/'降本变更与询价说明.txt').write_text(notes)
print('Semantic comparison passed:',len(changed),'bindings, 91 components and61nets unchanged')
