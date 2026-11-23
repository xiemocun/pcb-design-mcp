"""Explicit files-only policy; diagnostic uncertainties never become physical passes."""
from . import parts

# Closed list of unmodeled physics. Every new/unknown ERROR remains blocking.
# The original diagnostic report is preserved alongside this scoped view.
UNQUALIFIED_POWER = frozenset({
    'USB_SWITCH_BUDGET_CONDITIONAL', 'BUCK_INPUT_CURRENT_UPPER_BOUND_UNKNOWN',
    'BUCK_DYNAMIC_THERMAL_UNQUALIFIED', 'LOAD_AND_THERMAL_PROOF_INCOMPLETE',
    'USB_HARDWARE_TIMING_UNQUALIFIED', 'USB_CURRENT_CAPABILITY_UNVERIFIED',
})

def findings_for_geometry(findings):
    return [dict(f, severity='warning', diagnostic_severity=f['severity'],
                 disposition='requires_independent_prototype_review')
            if f['code'] in UNQUALIFIED_POWER else dict(f) for f in findings]


def assessment(service, s):
    """Eligibility to PREPARE a prototype review, never authorization to export."""
    from . import power_integrity, power_budget, module_contracts, usb_power
    led=(s['profile']=='led_series_experiment' or any(c['library_id']=='Device:LED' for c in s['components'].values())
         or any(p['kind']=='led' for p in s.get('part_bindings',{}).values()))
    spec=service.validate_state(s);bom=service.bom_check_state(s)
    findings=list(spec['findings']);reports=dict(specification=spec,bom=bom)
    if bom['identity_verdict']!='pass':
        findings.append(dict(code='BOM_IDENTITY_REQUIRED',severity='error',message='审阅要求全部器件有当前有效的来源与封装绑定。'))
    if led:
        engineering=service.engineering_state(s);reports['led_engineering']=engineering
        findings.extend(engineering['findings'])
        if engineering['verdict']!='pass':
            findings.append(dict(code='ENGINEERING_VALIDATION_REQUIRED',severity='error',message='LED 工程筛选未通过。'))
    if s['profile']!='led_series_experiment':
        power=power_integrity.evaluate(s);reports['power_integrity']=power
        findings.extend(findings_for_geometry(power['findings']))
        budget=power_budget.evaluate(s,bom);reports['declared_dc_budget']=budget
        findings.extend(budget['findings'])
        if budget['budget_verdict']!='conditional':
            findings.append(dict(code='DECLARED_BUDGET_REQUIRED',severity='error',message='原型审阅仍要求完整且无矛盾的声明直流预算。'))
        if any(c['library_id']=='Connector:USB_C_Receptacle_USB2.0_16P' for c in s['components'].values()):
            usb=usb_power.evaluate(s);reports['usb_power']=usb
            findings.extend(usb['findings'])
        if any(c['library_id']==module_contracts.SENSOR for c in s['components'].values()):
            module=module_contracts.evaluate(s);reports['port_contract']=module;findings.extend(module['findings'])
        missing=[('POWER_STARTUP_UNQUALIFIED','启动浪涌、有效电容及实物供电动态尚未验证。')]
        if any(c['library_id']=='RF_Module:ESP32-WROOM-32E' for c in s['components'].values()):
            missing += [('RESET_TIMING_UNQUALIFIED','实际复位波形及外部编程器时序尚未验证。'),('RF_FUNCTION_UNQUALIFIED','天线几何检查不能代替实际射频与功能验证。')]
        for code,message in missing:
            findings.append(dict(code=code,severity='warning',message=message))
    unique={}
    for f in findings:
        key=(f['code'],f.get('component_id'),tuple(f.get('entity_ids',[])),f['message'])
        unique[key]=f
    findings=list(unique.values())
    return dict(verdict='fail' if any(f['severity']=='error' for f in findings) else 'eligible_for_prototype_review',
                purpose='prototype_files_only',physical_qualified=False,manufacturing_approved=False,
                findings=findings,reports=reports,evidence_hash=parts.rules_hash(),
                qualification='prototype_review_eligibility_not_physical_or_production_qualification')
