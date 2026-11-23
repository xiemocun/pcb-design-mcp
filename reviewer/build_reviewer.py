"""Compile the macOS reviewer; optionally package one immutable review app."""
import argparse,json,plistlib,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from pcb_mcp import __version__
p=argparse.ArgumentParser();p.add_argument('--review',type=Path);p.add_argument('--authority-dir',type=Path,default=ROOT/'.approval-authority');args=p.parse_args()
binary=ROOT/'reviewer/pcb-reviewer'
subprocess.run(['xcrun','swiftc','-parse-as-library','-module-cache-path','/private/tmp/pcb-swift-cache',str(ROOT/'reviewer/Reviewer.swift'),'-o',str(binary)],check=True)
if args.review:
 review=args.review.resolve();assert (review/'approval_payload.json').is_file()
 app=review/'PCB 人工审阅.app';(app/'Contents/MacOS').mkdir(parents=True,exist_ok=True);(app/'Contents/Resources').mkdir(exist_ok=True)
 shutil.copy2(binary,app/'Contents/MacOS/pcb-reviewer')
 info=dict(CFBundleName='PCB 人工审阅',CFBundleDisplayName='PCB 人工审阅',CFBundleIdentifier='local.pcb-design-lab.reviewer',CFBundleExecutable='pcb-reviewer',CFBundlePackageType='APPL',CFBundleVersion=__version__,NSHighResolutionCapable=True,NSFaceIDUsageDescription='审批此固定版本 PCB 的制造文件导出，不下单。')
 (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info));(app/'Contents/Resources/review-request.json').write_text(json.dumps(dict(review=str(review),authority=str(args.authority_dir.resolve())),ensure_ascii=False))
 print(app)
else:print(binary)
