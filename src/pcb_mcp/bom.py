"""Identity coverage and declared passive-rating screen, never physical qualification."""
import copy,csv,io,json,re
from . import backend,parts
from .common import require,write_json

# Missing selections are allowed while drafting; contradictory identities are not.
IDENTITY_ERRORS={'PART_EVIDENCE_CHANGED','PART_BINDING_MISMATCH','ORPHAN_PART_BINDING','ORPHAN_RATING_REQUIREMENT'}

def natural_key(c):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)',c['ref'])]

class BOMService:
    def passive_ratings_set(self,request):
        def change(s):
            ids=[r.component_id for r in request.requirements]
            require(len(ids)==len(set(ids)),'DUPLICATE_RATING_REQUIREMENT','同一元件不能重复声明额定要求。')
            for r in request.requirements:
                c=s['components'].get(r.component_id)
                require(c is not None,'UNKNOWN_COMPONENT','额定要求指向不存在的元件。')
                require(c['library_id'] in ['Device:R','Device:C','Device:C_Polarized'],'RATING_ROLE_UNSUPPORTED','本工具仅支持电阻和两脚电容。')
                if c['library_id']=='Device:R':
                    require(r.min_resistor_rated_power_w is not None and not r.allowed_dielectrics and r.polarized_connection is None,'RATING_ROLE_MISMATCH','电阻必须声明最低额定功率，不能设置电介质或极性。')
                else:
                    require(r.min_resistor_rated_power_w is None and bool(r.allowed_dielectrics),'RATING_ROLE_MISMATCH','电容必须声明允许的电介质，不能设置电阻功率。')
                    require((c['library_id']=='Device:C_Polarized')==(r.polarized_connection is not None),'RATING_POLARITY_REQUIRED','极性电容必须显式声明正脚接电源、负脚接地；非极性电容不接受此规则。')
                require(len(r.allowed_dielectrics)==len(set(r.allowed_dielectrics)),'DUPLICATE_DIELECTRIC','电介质列表重复。')
            # Atomic replacement prevents hidden leftover assumptions.
            s['passive_rating_requirements']=request.model_dump(exclude={'project_id','expected_revision','idempotency_key'})
            return dict(entity_id=s['id'])
        return self.mutate('passive_ratings_set',request,change)

    def bom_check_state(self,s):
        current={p['candidate_id']:p for p in parts.catalog()};rows=[];findings=[]
        policy=s.get('passive_rating_requirements',{})
        requirements={r['component_id']:r for r in policy.get('requirements',[])}
        assignment={(p['component_id'],p['pin_id']):n for n in s['nets'].values() for p in n['pins']}
        counts=dict(total=len(s['components']),bound=0,identity_checked=0,missing=0,invalid=0,passive_total=0,passive_screened=0,supplier_unselected=0)
        def add(code,c,message,severity='warning'):
            findings.append(dict(code=code,severity=severity,component_id=c['id'],reference=c['ref'],message=message))
        for cid in set(s.get('part_bindings',{}))-set(s['components']):
            add('ORPHAN_PART_BINDING',dict(id=cid,ref='?'),'实物绑定指向不存在的元件。','error')
        for cid in set(requirements)-set(s['components']):
            add('ORPHAN_RATING_REQUIREMENT',dict(id=cid,ref='?'),'额定要求指向不存在的元件。','error')
        for c in sorted(s['components'].values(),key=natural_key):
            p=s.get('part_bindings',{}).get(c['id']);fp=s['footprint_bindings'].get(c['id'],{})
            row=dict(component_id=c['id'],reference=c['ref'],value=c['value'],symbol=c['library_id'],footprint=fp.get('footprint_id'),manufacturer=None,mpn=None,identity_status='missing',rating_status='not_covered',sources=[],supplier_selection='not_selected',physical_qualified=False)
            passive=c['library_id'] in ['Device:R','Device:C','Device:C_Polarized']
            if passive:counts['passive_total']+=1;row['rating_status']='incomplete'
            if not p:
                counts['missing']+=1;add('PART_BINDING_MISSING',c,'尚未选择有来源、数值和封装匹配的实物型号。')
            else:
                counts['bound']+=1
                row.update(manufacturer=p['manufacturer'],mpn=p['mpn'],sources=p['sources'],facts=p['facts'],part_evidence_hash=p['definition_hash'],package_validation=p['facts'].get('package_validation','not_recorded'),supplier_selection='recorded_static_sku' if p['skus'] else 'not_selected')
                if not p['skus']:counts['supplier_unselected']+=1
                if current.get(p['candidate_id'])!=p or p['evidence_status']!='available':
                    counts['invalid']+=1;row['identity_status']='invalid';add('PART_EVIDENCE_CHANGED',c,'厂家资料、目录或封装定义已变化。','error')
                elif not self.part_binding_valid(s,c,p):
                    counts['invalid']+=1;row['identity_status']='invalid';add('PART_BINDING_MISMATCH',c,'实物型号与当前符号、标称值或真实封装不一致。','error')
                else:
                    counts['identity_checked']+=1;row['identity_status']='checked'
                if passive and row['identity_status']=='checked':
                    req=requirements.get(c['id']);row['rating_requirement']=req;row['rating_requirement_origin']=policy.get('origin')
                    if not req:add('PASSIVE_REQUIREMENT_MISSING',c,'缺少电压、精度和功率/电介质要求，无法筛选。')
                    else:
                        facts=p['facts'];tests=[('voltage_rating_v',req['max_applied_voltage_v'],'min'),('tolerance_fraction',req['max_tolerance_fraction'],'max')]
                        if p['kind']=='resistor':tests.append(('rated_power_w',req['min_resistor_rated_power_w'],'min'))
                        results=[]
                        for key,limit,relation in tests:
                            observed=facts.get(key)
                            ok=None if observed is None else observed>=limit if relation=='min' else observed<=limit
                            results.append(dict(fact=key,observed=observed,required=limit,relation=relation,status='unknown' if ok is None else 'within_declared_limit' if ok else 'fail'))
                        if p['kind']=='capacitor':
                            observed=facts.get('dielectric');ok=None if observed is None else observed in req['allowed_dielectrics']
                            results.append(dict(fact='dielectric',observed=observed,required=req['allowed_dielectrics'],relation='one_of',status='unknown' if ok is None else 'within_declared_limit' if ok else 'fail'))
                            if facts.get('polarized'):
                                mapping=facts.get('pin_mapping',{})
                                pin_numbers={pin['number']:pin['pin_id'] for pin in c['pins']}
                                pos=[num for num,role in mapping.items() if role=='positive'];neg=[num for num,role in mapping.items() if role=='negative']
                                known=len(pos)==len(neg)==1 and pos[0] in pin_numbers and neg[0] in pin_numbers
                                plus=assignment.get((c['id'],pin_numbers[pos[0]])) if known else None
                                minus=assignment.get((c['id'],pin_numbers[neg[0]])) if known else None
                                declared=req.get('polarized_connection')=='positive_to_power_negative_to_ground'
                                status=('unknown' if not known or not declared or not plus or not minus else
                                        'within_declared_limit' if plus['kind']=='power' and minus['kind']=='ground' else
                                        'fail' if plus['kind']=='ground' and minus['kind']=='power' else 'unknown')
                                results.append(dict(fact='declared_polarity',positive_net=plus['name'] if plus else None,negative_net=minus['name'] if minus else None,required=req.get('polarized_connection'),status=status))
                                if status=='fail':add('POLARIZED_CAP_REVERSED',c,'极性电容的正负脚违反已声明的电源/接地连接规则。','error')
                                add('ELECTROLYTIC_STRESS_UNQUALIFIED',c,'仅核对声明极性及标称额定值；实际反向/浪涌电压、纹波、阻抗、温度和寿命仍未验证。')
                            else:add('EFFECTIVE_CAPACITANCE_UNQUALIFIED',c,'标称容量不能代替直流偏压、温度、老化和公差下的有效容量；尚未验证。')
                        row['rating_checks']=results
                        row['rating_status']='fail' if any(t['status']=='fail' for t in results) else 'incomplete' if any(t['status']=='unknown' for t in results) else 'conditional'
                        if row['rating_status']=='conditional':counts['passive_screened']+=1
                        else:add('PASSIVE_RATING_FAILED' if row['rating_status']=='fail' else 'PASSIVE_RATING_UNKNOWN',c,'实物额定值不满足声明，或厂家资料缺少所需额定值。','error' if row['rating_status']=='fail' else 'warning')
            rows.append(row)
        identity='fail' if any(f['code'] in IDENTITY_ERRORS for f in findings) else 'pass' if counts['total'] and counts['identity_checked']==counts['total'] else 'incomplete'
        rating='fail' if any(r['rating_status']=='fail' for r in rows) else 'conditional' if counts['passive_total'] and counts['passive_screened']==counts['passive_total'] else 'incomplete'
        findings.append(dict(code='PHYSICAL_AND_FUNCTIONAL_QUALIFICATION_MISSING',severity='warning',message='封装公差/试装、实际应力/温升、有效容量、功能、供应链和可生产性均未认证。'))
        return dict(project_id=s['id'],revision=s['revision'],verdict='fail' if any(f['severity']=='error' for f in findings) else 'incomplete',identity_verdict=identity,declared_rating_screen=rating,counts=counts,rows=rows,findings=findings,evidence_hash=parts.rules_hash(),physical_qualified=False,manufacturing_approved=False,qualification='source_linked_identity_and_declared_nominal_rating_screen_only_not_production_qualification')

    def bom_check(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            return self.bom_check_state(s)

    def bom_draft_export(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            report=self.bom_check_state(s);require(report['rows'],'EMPTY_BOM','没有元件可导出。')
            folder=self.project_path(s['id'])/'exports';folder.mkdir(exist_ok=True)
            path=folder/('bom-draft-r'+str(s['revision'])+'.json');write_json(path,report)
            fields=['reference','value','manufacturer','mpn','footprint','identity_status','rating_status','supplier_selection','physical_qualified']
            out=io.StringIO();w=csv.DictWriter(out,fieldnames=fields);w.writeheader()
            for row in report['rows']:
                # Spreadsheet-safe draft; absent MPN stays blank, never guessed.
                w.writerow({k:("'"+row[k] if isinstance(row[k],str) and row[k].startswith(('=','+','-','@','\t','\r')) else row[k]) for k in fields})
            csvpath=path.with_suffix('.csv');csvpath.write_text(out.getvalue())
            return dict(project_id=s['id'],revision=s['revision'],json_path=str(path),csv_path=str(csvpath),json_sha256=backend.digest(path.read_bytes()),csv_sha256=backend.digest(csvpath.read_bytes()),report=report,qualification='incomplete_draft_for_review_not_procurement_or_manufacturing_output')
