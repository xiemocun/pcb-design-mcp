"""Server-owned reference placement; no model coordinates or arbitrary profiles."""
import math

ESP='RF_Module:ESP32-WROOM-32E'
SENSOR='PCB_MCP:AM2302_SIP4'
LDO='Regulator_Linear:TLV75533PDBV'
USB='Connector:USB_C_Receptacle_USB2.0_16P'
TXS='Logic_LevelTranslator:TXS0102DCT'
BUCK='PCB_MCP:R78K3V3_1A'
try:from .power_stage_roles import BUCKS,PWM_BUCK
except ImportError:from power_stage_roles import BUCKS,PWM_BUCK
CC='PCB_MCP:TUSB320LAI_GPIO'
INV='PCB_MCP:SN74LVC1G14_DBV'
SW='PCB_MCP:TPS2553DBV'

def reference_plan(s):
    from collections import Counter
    comps=list(s['components'].values());counts=Counter(c['library_id'] for c in comps)
    with_control=bool(counts[CC] or counts[INV] or counts[SW])
    bucks=[lib for lib in BUCKS if counts[lib]]
    if len(bucks)>1:raise ValueError('REFERENCE_CORE_ROLE_AMBIGUOUS: buck')
    buck_lib=bucks[0] if bucks else BUCK;with_buck=bool(bucks);with_txs=bool(counts[TXS]);with_pwm=buck_lib==PWM_BUCK
    try:from .signal_roles import roles as source_roles
    except ImportError:from signal_roles import roles as source_roles
    signal_parts=source_roles(s);with_interface=with_txs or bool(signal_parts)
    required={ESP:1,SENSOR:1,USB:1,'Connector_Generic:Conn_01x06':1,buck_lib if with_buck else LDO:1}
    if with_control:required.update({CC:1,INV:1,SW:1,LDO:1})
    if any(counts[lib]<n for lib,n in required.items()):raise ValueError('REFERENCE_REQUIRED_COMPONENT_MISSING')
    for lib in required:
        if counts[lib]>1:raise ValueError('REFERENCE_CORE_ROLE_AMBIGUOUS: '+lib)
    # The server validates the port contract in preflight. Keep placement
    # stdlib-only: the KiCad Python 3.9 worker imports this as a standalone module.
    dims=s['board_constraints'];w,h=dims['width_mm'],dims['height_mm']
    if w<80 or h<65:raise ValueError('REFERENCE_BOARD_TOO_SMALL: minimum 80x65mm')
    if with_control and (w<120 or h<100):raise ValueError('REFERENCE_BOARD_TOO_SMALL: controlled USB reference needs 120x100mm')
    assigned={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    def part(lib):return next(c for c in comps if c['library_id']==lib)
    def net(c,name):return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    def number(c):
        import re
        m=re.fullmatch(r'([\d.]+)([pnumkM]?)(?:F)?',c['value'])
        if not m:raise ValueError('REFERENCE_VALUE_UNSUPPORTED')
        return float(m[1])*{'':1,'p':1e-12,'n':1e-9,'u':1e-6,'m':1e-3,'k':1e3,'M':1e6}[m[2]]
    esp,sensor,ldo,usb,j=[part(x) for x in [ESP,SENSOR,buck_lib if with_buck else LDO,USB,'Connector_Generic:Conn_01x06']]
    used=set();placements={}
    def place(c,x,y,angle=0.0):
        if c['id'] in used:raise ValueError('REFERENCE_ROLE_CONFLICT')
        used.add(c['id']);placements[c['id']]=[x,y,angle]
    def passive(lib,a,b,lo,hi):
        matches=[c for c in comps if c['library_id']==lib and c['id'] not in used and {assigned.get((c['id'],p['pin_id'])) for p in c['pins']}=={a,b} and lo*.999<=number(c)<=hi*1.001]
        if not matches:raise ValueError('REFERENCE_PASSIVE_ROLE_MISSING')
        import re
        return sorted(matches,key=lambda c:(number(c),int(re.search(r'\d+$',c['ref']).group()),c['ref']))[0]
    gnd=net(esp,'GND');v=net(esp,'VDD');vi=net(ldo,'IN')
    place(esp,w/2,15);place(ldo,w/2-11.54 if with_buck else w/2-9,45 if with_buck else 39);place(sensor,w-18.5 if with_interface else w-20,42)
    place(usb,w/2,h-3);place(j,10,43)
    if with_pwm:
        x,y=placements[ldo['id']][:2]
        place(passive('Device:C',vi,gnd,10e-6,10e-6),x,y-2.75,0.)
        place(passive('Device:C',v,gnd,10e-6,10e-6),x+2.75,y,90.)
        # Feedback roles follow the actual net, without accepting client coordinates.
        fb=net(ldo,'FB')
        place(passive('Device:R',v,fb,453000,453000),x-3.25,y,180.)
        place(passive('Device:R',fb,gnd,100000,100000),x-4.25,y-2.5)
        place(passive('Device:C',v,fb,120e-12,120e-12),x-3.25,y+2.5,180.)
    else:
        place(passive('Device:C',vi,gnd,1e-6,1e-6),w/2-11.54 if with_buck else w/2-13,57 if with_buck else 38.05,180.0)
        place(passive('Device:C',v,gnd,1e-6,1e-6),w/2-6.46 if with_buck else w/2-5,57 if with_buck else 38.05)
    if with_buck:place(passive('Device:C_Polarized',vi,gnd,27e-6,27e-6),w/2-24,50)
    place(passive('Device:C',v,gnd,10e-6,10e-6),w/2-13,15.5)
    place(passive('Device:C',v,gnd,1e-7,1e-7),w/2-12,11)
    if with_pwm:
        x,y=placements[ldo['id']][:2];place(passive('Device:C',v,gnd,10e-6,10e-6),x+4.75,y,90.)
    place(passive('Device:C',net(sensor,'VDD'),gnd,1e-7,1e-7),w-18.5 if with_interface else w-20,38)
    place(passive('Device:C',net(esp,'EN'),gnd,1e-6,1e-6),w/2-16,21)
    if not with_control:
        place(passive('Device:R',net(usb,'CC1'),gnd,5100,5100),w/2-6,h-13)
        place(passive('Device:R',net(usb,'CC2'),gnd,5100,5100),w/2+6,h-13)
    place(passive('Device:R',net(sensor,'SDA'),net(sensor,'VDD'),10000 if with_interface else 5100,10000 if with_interface else 5100),w-25 if with_buck else w-28,48 if with_buck else 46)
    place(passive('Device:R',net(esp,'EN'),v,10000,10000),w/2-16,25)
    place(passive('Device:R',net(esp,'IO0'),v,10000,10000),w/2-16,30)
    if with_txs:
        txs=part(TXS);x,y=w-30,42
        place(txs,x,y)
        place(passive('Device:C',net(txs,'VCCA'),gnd,1e-7,1e-7),x-4.5,y+.325,180.0)
        place(passive('Device:C',net(txs,'VCCB'),gnd,1e-7,1e-7),x+4.5,y-.325)
        place(passive('Device:R',net(txs,'OE'),gnd,100000,100000),x-4,y+5)
        place(passive('Device:R',net(txs,'A2'),gnd,100,100),x-4,y-3)
    for index,(c,f) in enumerate(sorted(signal_parts,key=lambda x:x[0]['ref'])):
        x,y=w-30,40+7*index
        # Face receiver input toward the sensor; driver input toward the MCU.
        receive=f['signal_role']=='schmitt_receiver'
        place(c,x,y,180.0 if receive else 0.0)
        place(passive('Device:C',v,gnd,1e-7,1e-7),x-3.8 if receive else x+3.8,y+1.1 if receive else y-1.1,180.0 if receive else 0.0)
        if f['signal_role']=='open_drain_driver':
            inp=next(assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==f['input_pin_number'])
            place(passive('Device:R',inp,v,4700,10000),x-4,y+2)
    if with_control:
        cc,inv,sw,aux=[part(lib) for lib in (CC,INV,SW,LDO)]
        # Server-owned prototype placement. Local part coordinates never come
        # from a client request; actual geometry/proximity/DRC remain mandatory.
        dx=w/2-65;dy=h-100
        place(cc,48+dx,80+dy);place(inv,63+dx,76+dy)
        place(sw,78.4+dx,76+dy);place(aux,74+dx,76+dy)
        raw,logic=net(sw,'IN'),net(aux,'OUT')
        place(passive('Device:C',logic,gnd,1e-7,1e-7),48.175+dx,76.5+dy)
        place(passive('Device:C',logic,gnd,1e-7,1e-7),66.8+dx,74.9+dy)
        place(passive('Device:C',raw,gnd,1e-6,1e-6),75.05+dx,72.3+dy,90.0)
        place(passive('Device:C',logic,gnd,1e-6,1e-6),77.7+dx,72.8+dy)
        place(passive('Device:R',raw,net(cc,'VBUS_DET'),887000,887000),43+dx,86+dy)
        place(passive('Device:R',logic,net(cc,'OUT1'),10000,10000),55+dx,77+dy)
        place(passive('Device:R',net(sw,'EN'),gnd,100000,100000),69.5+dx,79+dy)
        place(passive('Device:R',net(sw,'ILIM'),gnd,15000,232000),82.7+dx,74.9+dy)
    # Remaining bound components are placed by pcb_worker using actual geometry.
    # Missing required roles above still fail closed; additions are not discarded.
    return placements

def reference_text_plan(s):
    """Keep the two new power references clear of their input/output caps."""
    if not any(c['library_id']==CC for c in s['components'].values()):return {}
    dx=s['board_constraints']['width_mm']/2-65;dy=s['board_constraints']['height_mm']-100
    return {c['id']:[(78.4 if c['library_id']==SW else 74)+dx,79.5+dy]
            for c in s['components'].values() if c['library_id'] in (SW,LDO)}
