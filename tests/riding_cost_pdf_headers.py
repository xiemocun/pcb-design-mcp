from pathlib import Path
from pypdf import PdfReader,PdfWriter
from reportlab.pdfgen import canvas
import io,json
O=Path('work/riding-intercom/cost-down/vendor-package');rev=json.loads((O/'cost-down-verification.json').read_text())['revision']
for name,titles in [('top-assembly.pdf',['Top assembly / component references and pin numbers']),('copper-and-mask-review.pdf',['Front copper / top view','Back copper / top view, not mirrored','Front solder mask openings / no drill overlay','Back solder mask openings / no drill overlay'])]:
 reader=PdfReader(O/name);writer=PdfWriter();assert len(reader.pages)==len(titles)
 for page,title in zip(reader.pages,titles):
  w,h=float(page.mediabox.width),float(page.mediabox.height);buf=io.BytesIO();c=canvas.Canvas(buf,pagesize=(w,h));c.setFont('Helvetica-Bold',14);c.drawString(36,h-32,title);c.setFont('Helvetica',9);c.drawString(36,h-49,'Riding intercom cost-down / revision '+str(rev)+' / Native KiCad vector plot / Use Gerber and drill files for fabrication');c.drawString(36,22,'Board 90 x 65 mm. Page plot scale 2:1 before any print-to-fit scaling.');c.showPage();c.save();page.merge_page(PdfReader(buf).pages[0]);writer.add_page(page)
 with (O/name).open('wb') as f:writer.write(f)
