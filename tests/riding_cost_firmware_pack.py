"""Package rebuilt A/B images only after byte-for-byte input/output verification."""
import json,hashlib,shutil,subprocess
from pathlib import Path
root=Path('work/riding-intercom').resolve();repo=Path('.').resolve();out=root/'cost-down/vendor-package/firmware';out.mkdir(exist_ok=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
records={}
for role in ['a','b']:
 build=root/('idf-'+role);record=json.loads((build/'build-record.json').read_text());assert all(sha(repo/p)==h for p,h in record['inputs'].items()),'Stale firmware inputs';assert all(sha(build/p)==r['sha256'] for p,r in record['outputs'].items()),'Stale firmware outputs';target=out/('unit-'+role);target.mkdir(exist_ok=True)
 for name in ['riding_intercom.bin','bootloader/bootloader.bin','partition_table/partition-table.bin','srmodels/srmodels.bin','flash_args','flasher_args.json','build-record.json']:
  dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(build/name,dest)
 records[role]=dict(app_sha256=sha(build/'riding_intercom.bin'),role=role,physical_run_verified=False)
assert records['a']['app_sha256']!=records['b']['app_sha256']
s=json.loads((root/'data/89184e4c1e4740f88be798dfed22a98b/state.json').read_text());c=next(c for c in s['components'].values() if c['ref']=='U1');names={p['pin_id']:p['name'] for p in c['pins']};expected={'I2C_SDA':'IO17','I2C_SCL':'IO18','I2S_MCLK':'IO16','I2S_BCLK':'IO9','I2S_LRCK':'IO45','I2S_ADC_DATA':'IO10','I2S_DAC_DATA':'IO8','PA_EN':'IO48','KEY_MUTE':'IO4','KEY_UP':'IO5','KEY_DOWN':'IO6'}
for n in s['nets'].values():
 if n['name'] in expected:
  members=[p for p in n['pins'] if p['component_id']==c['id']];assert len(members)==1 and names[members[0]['pin_id']]==expected[n['name']],n['name']
(out/'hardware-binding.json').write_text(json.dumps(dict(project_id=s['id'],revision=s['revision'],pcb_build_id=json.loads((root/'cost-down/pcb-01/pcb_build.json').read_text())['pcb_build_id'],gpio=expected,firmware=records,physical_verified=False),indent=2))
(out/'烧录说明.txt').write_text('两台相同硬件分别烧录 unit-a 与 unit-b，不要两台都烧同一角色。\n用3.3V UART，板TX接下载器RX，板RX接下载器TX，共地。电源由板上J1供给；J6第6脚仅电压检测，不反向供电。BOOT拉低时复位进入下载模式。\n安装 esptool 后，在对应 unit-a 或 unit-b 文件夹执行：\npython -m esptool --chip esp32s3 --port <实际串口> write_flash @flash_args\n需要按键手动进下载模式时，可调整 esptool 的复位选项。\n四个地址均须烧录，包括0x610000模型分区；所有文件按同包flash_args使用。\n初始音量45，实体按钮：J7的1-2静音、3-4音量+、5-6音量-。串口115200，m/+/-同效。\n这些镜像经过真实Xtensa编译与文件一致性检查，尚未在实物上运行验收。\n')
lic=out/'licenses';lic.mkdir(exist_ok=True)
for component in ['esp_codec_dev','esp_audio_codec','esp-sr','esp-dl','dl_fft','cjson','esp_new_jpeg']:
 for p in (root/'deps'/component).rglob('*'):
  if p.is_file() and any(x in p.name.upper() for x in ['LICENSE','COPYING','NOTICE']):
   dest=lic/component/p.relative_to(root/'deps'/component);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
print('Actual rebuilt A/B firmware packaged and checked against current PCB GPIO')
