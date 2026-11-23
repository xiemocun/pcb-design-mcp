"""Readable vector component sheets cropped from the verified KiCad PDF."""
import json,copy,io,sys
from pathlib import Path
from pypdf import PdfReader,PdfWriter,Transformation
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas
from reportlab.lib import colors
root=Path('work/riding-intercom');out=root/'cost-down/vendor-package';s=json.loads((root/'data/89184e4c1e4740f88be798dfed22a98b/state.json').read_text());design=json.loads((root/'hardware-circuit/design.json').read_text());pos=json.loads((out/'schematic-positions.json').read_text());original=PdfReader(out/'schematic.pdf').pages[0];PT=72/25.4;sourceheight=float(original.mediabox.height)
order=['charger','boost','buck','audio_power','mcu','adc','mic1','mic2','dac','aec','pa','keys'];writer=PdfWriter();W,H=841.89,1190.55;M=28;usable=W-2*M
page=None;pageparts=[];records=[]
def finish(group,number):
 global pageparts
 if not pageparts:return
 buf=io.BytesIO();c=canvas.Canvas(buf,pagesize=(W,H));c.setFont('Helvetica-Bold',18);c.drawString(M,H-34,'Riding intercom / '+group+' / '+str(number));c.setFont('Helvetica',9);c.drawString(M,H-52,'Revision '+str(s['revision'])+' | Exact vector crops from checked native schematic')
 c.drawString(M,16,'Net labels connect across views. This drawing does not replace the complete editable schematic.')
 for ref,rect,target in pageparts:
  x0,y0,x1,y1=target;c.setStrokeColor(colors.HexColor('#c4ced6'));c.rect(x0,y0,x1-x0,y1-y0);c.setFont('Helvetica-Bold',11);c.setFillColor(colors.black);c.drawString(x0+8,y1-17,ref+' / '+next(v['value'] for v in s['components'].values() if v['ref']==ref))
 c.showPage();c.save();p=PdfReader(buf).pages[0]
 for ref,rect,target in pageparts:
  x0,y0,x1,y1=target;rx0,ry0,rx1,ry1=rect;scale=min((x1-x0-12)/(rx1-rx0),(y1-y0-32)/(ry1-ry0));dx=x0+(x1-x0-(rx1-rx0)*scale)/2;dy=y0+5
  crop=copy.copy(original);crop.cropbox=RectangleObject(rect);crop.trimbox=RectangleObject(rect)
  t=Transformation().translate(-rx0,-ry0).scale(scale).translate(dx,dy);p.merge_transformed_page(crop,t,over=True)
 writer.add_page(p);records.append(dict(group=group,page=len(writer.pages),references=[p[0] for p in pageparts]));pageparts=[]
for group in order:
 comps=sorted([c for c in s['components'].values() if design['groups'][c['ref']]==group],key=lambda c:(not c['ref'].startswith(('U','MK','J','L')),c['ref']))
 y=H-72;number=1;pending=[]
 for component in comps:
  ref=component['ref'];x,sy=pos[ref];ex=max([abs(float(p['at'][0])) for p in component['pins']]+[5]);ey=max([abs(float(p['at'][1])) for p in component['pins']]+[5]);big=len(component['pins'])>12
  left=ex+20 if big else 17;right=ex+57 if big else 35;vert=ey+15
  rect=((x-left)*PT,sourceheight-(sy+vert)*PT,(x+right)*PT,sourceheight-(sy-vert)*PT)
  width=usable if big else (usable-12)/2;scale=min(2.2,(width-12)/(rect[2]-rect[0]));height=(rect[3]-rect[1])*scale+34
  if big and pending:
   y-=max(v[3] for v in pending)+10
   for r,rc,xx,hh in pending:pageparts.append((r,rc,(xx,y+10,xx+(usable-12)/2,y+10+hh)))
   pending=[]
  if y-height<35:
   if pending:
    y-=max(v[3] for v in pending)+10
    for r,rc,xx,hh in pending:pageparts.append((r,rc,(xx,y+10,xx+(usable-12)/2,y+10+hh)))
    pending=[]
   finish(group,number);number+=1;y=H-72
  if big:pageparts.append((ref,rect,(M,y-height,M+width,y)));y-=height+10
  else:
   xx=M+len(pending)*(width+12);pending.append((ref,rect,xx,height))
   if len(pending)==2:
    rowh=max(v[3] for v in pending);y-=rowh+10
    for r,rc,xx,hh in pending:pageparts.append((r,rc,(xx,y+10,xx+width,y+10+hh)))
    pending=[]
 if pending:
  y-=max(v[3] for v in pending)+10
  for r,rc,xx,hh in pending:pageparts.append((r,rc,(xx,y+10,xx+(usable-12)/2,y+10+hh)))
 finish(group,number)
assert sorted(r for page in records for r in page['references'])==sorted(c['ref'] for c in s['components'].values())
[p.compress_content_streams() for p in writer.pages]
writer.write(out/'functional-schematic.pdf');(out/'functional-schematic-index.json').write_text(json.dumps(records,indent=2));print(len(records),'pages; all91 components preserved as native vector crops')
