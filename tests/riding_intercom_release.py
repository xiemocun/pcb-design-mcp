"""Check real artifacts, frozen MCP implementation and linked audio features.
Does not claim that Xtensa firmware or RF/acoustic processing ran on hardware.
"""
import hashlib,json,subprocess,zipfile
from pathlib import Path

REPO=Path(__file__).resolve().parent.parent
WORK=REPO/'work/riding-intercom'
FW=REPO/'firmware/riding-intercom'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    number=1
    while (WORK/f'release-{number:02d}').exists():number+=1
    out=WORK/f'release-{number:02d}';out.mkdir(exist_ok=False)
    frozen=json.loads((REPO/'work/generalization/freeze-01.json').read_text())
    assert all(sha(REPO/p)==h for p,h in frozen.items()),'MCP core changed'
    expected={'I2C_SDA':17,'I2C_SCL':18,'I2S_MCLK':16,'I2S_BCLK':9,'I2S_LRCK':45,'I2S_ADC_DATA':10,'I2S_DAC_DATA':8}
    state=json.loads((WORK/'electrical-draft-02/state.json').read_text())
    mcu=next(c for c in state['components'].values() if c['ref']=='U1')
    pin_names={p['pin_id']:p['name'] for p in mcu['pins']}
    for net in state['nets'].values():
        pins=[p for p in net['pins'] if p['component_id']==mcu['id']]
        assert len(pins)==1 and pin_names[pins[0]['pin_id']]=='IO'+str(expected[net['name']])
    code=(FW/'src/board_audio.c').read_text()
    for field,gpio in [('sda_io_num',17),('scl_io_num',18),('mclk',16),('bclk',9),('ws',45),('dout',8),('din',10)]:
        assert f'.{field} = GPIO_NUM_{gpio}' in code
    nm=(REPO/'../../work/interface-redesign/platformio/packages/toolchain-xtensa-esp-elf/bin/xtensa-esp32s3-elf-nm').resolve()
    records={}
    linked=['app_main','esp_opus_enc_process','esp_opus_dec_decode','esp_afe_handle_from_config','aec_create_from_config','esp_nsnet2_quantized','esp_wifi_start']
    for role in 'ab':
        build=WORK/('idf-'+role);r=json.loads((build/'build-record.json').read_text())
        assert all(sha(REPO/p)==h for p,h in r['inputs'].items()),'Stale source '+role
        assert all(sha(build/p)==v['sha256'] for p,v in r['outputs'].items()),'Stale artifact '+role
        commands=json.loads((build/'compile_commands.json').read_text())
        radio=next(x['command'] for x in commands if x['file']==str(FW/'src/radio.c'))
        assert '-DRIDE_ROLE_AP='+str(int(role=='a')) in radio
        symbols=subprocess.check_output([str(nm),'-g',str(build/'riding_intercom.elf')],text=True)
        for name in linked:assert any(line.endswith(' '+name) and ' U ' not in line for line in symbols.splitlines()),name
        config=(build/'sdkconfig').read_text()
        for feature in ('CONFIG_SR_NSN_NSNET2=y','CONFIG_SPIRAM_MODE_OCT=y','CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y','CONFIG_COMPILER_OPTIMIZATION_ASSERTIONS_ENABLE=y'):
            assert feature in config
        flasher=json.loads((build/'flasher_args.json').read_text())
        assert flasher['flash_files']['0x610000']=='srmodels/srmodels.bin'
        assert (build/'srmodels/srmodels.bin').stat().st_size>10000
        records[role]=r
    assert records['a']['outputs']['riding_intercom.bin']['sha256']!=records['b']['outputs']['riding_intercom.bin']['sha256']
    binary=out/'transport-test'
    subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g',
                    '-I'+str(FW/'src'),str(REPO/'tests/riding_transport_test.c'),str(FW/'src/transport.c'),'-o',str(binary)],check=True)
    test=subprocess.check_output([str(binary)],text=True)
    (out/'transport-test.log').write_text(test)
    archive=out/'riding-intercom-reference-prototype.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.write(FW/'README.md','README.md')
        z.write(WORK/'原型设计.md','原型设计.md')
        for role in 'ab':
            build=WORK/('idf-'+role)
            for n in ('riding_intercom.bin','bootloader/bootloader.bin','partition_table/partition-table.bin','srmodels/srmodels.bin','flasher_args.json','build-record.json'):
                z.write(build/n,'unit-'+role+'/'+n)
        for component in ('esp_codec_dev','esp_audio_codec','esp-sr','esp-dl','dl_fft','cjson','esp_new_jpeg'):
            for license_file in (WORK/'deps'/component).rglob('*'):
                if license_file.is_file() and any(w in license_file.name.upper() for w in ('LICENSE','COPYING','NOTICE')):
                    z.write(license_file,'licenses/'+component+'/'+str(license_file.relative_to(WORK/'deps'/component)))
    result=dict(passed=True,scope='host transport behavior + two real Xtensa builds and source/feature checks',
        core_freeze_unchanged=True,mcp_project_id=state['id'],mcp_revision=state['revision'],
        interface_consistency=True,host_transport_test=test.strip(),linked_features=linked,
        binaries={u:r['outputs']['riding_intercom.bin'] for u,r in records.items()},
        package=dict(path=str(archive),sha256=sha(archive),bytes=archive.stat().st_size),
        physical_verified=False,firmware_runtime_verified=False,schematic_complete=False,pcb_generated=False,
        urban_1km_verified=False,acoustic_quality_verified=False)
    (out/'verification.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(result,indent=2,ensure_ascii=False))

if __name__=='__main__': main()
