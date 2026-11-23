"""Chinese factory instructions generated from exact checked board/review data."""
import json,sys,hashlib,shutil,csv
from pathlib import Path
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
root=Path('work/riding-intercom').resolve();review=Path(sys.argv[1]);out=root/'cost-down/vendor-package';out.mkdir(exist_ok=True)
s=json.loads((root/'data/89184e4c1e4740f88be798dfed22a98b/state.json').read_text());g=json.loads((review/'assembly-geometry.json').read_text());payload=json.loads((review/'approval_payload.json').read_text())
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='CN',fontName='STSong-Light',fontSize=10,leading=16,spaceAfter=8,wordWrap='CJK'));styles.add(ParagraphStyle(name='CNTitle',fontName='STSong-Light',fontSize=20,leading=26,spaceAfter=16));styles.add(ParagraphStyle(name='CNHead',fontName='STSong-Light',fontSize=13,leading=19,spaceAfter=10));styles.add(ParagraphStyle(name='CNsmall',fontName='STSong-Light',fontSize=8,leading=12,wordWrap='CJK'))
P=lambda t:Paragraph(t,styles['CN']);story=[]
def title(t):story.append(Paragraph(t,styles['CNTitle']))
def head(t):story.append(Paragraph(t,styles['CNHead']))
def para(t):story.append(P(t))
def table(rows,widths):
 cells=[[Paragraph(str(v),styles['CNsmall']) for v in r] for r in rows];t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT');t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9f0f5')),('GRID',(0,0),(-1,-1),.4,colors.HexColor('#b8c5cf')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]));story.append(t);story.append(Spacer(1,12))
def page():story.append(PageBreak())
assert not g['fill_cap_holes'],'Special via process remains'
title('骑行对讲机 / 降本版 PCBA')
para('用途：两台相同硬件分别烧录 A / B 固件，离线双向语音、机身扬声器。文件用于首轮工程样机的制板和贴片；城市遮挡 1 公里、骑行风噪与最大音量属于有板后的验证目标。')
para('工程版本：'+str(s['revision'])+'；PCB 构建：'+payload['pcb_build_id']+'。所有制造文件必须采用同一包，禁止与历史目录混用。')
table([['项目','制造要求'],['板材 / 层数','FR-4，2 层，板厚 1.6 mm（公差 ±0.16 mm），成品外层铜厚至少 35 μm。'],['外形 / 安装','90 × 65 mm；以 Edge.Cuts 为准。本版无安装孔，采用绝缘边槽或边缘夹持固定；禁止现场钻孔。'],['表面 / 阻焊','无铅 ENIG；双面绿色阻焊、顶层白色丝印；电气开窗按文件。'],['工艺能力','最小线宽 / 间距 0.15 / 0.15 mm；最小成品孔 0.20 mm；通孔钻孔间隙按设计规则至少 0.25 mm。'],['过孔工艺','不需要树脂填孔、铜帽盖孔、盲埋孔或激光微孔。普通机械通孔，过孔双面盖油；U1 中心按文件分格开窗，禁止把散热孔重新开窗。filled-capped-holes.csv 应只有表头（0 个）。'],['钢网 / 装配','顶面贴片，建议 0.10 mm 激光钢网；开孔以 F.Paste 为依据。J1–J7 排针回流后手工 / 选择焊，所有器件均装配，无 DNP。'],['厂内检验','裸板 100% 电测；贴片 AOI；U1/U2/U3/U4 的底部 / 裸露焊盘用 X-ray 检查连锡、开路与明显空洞。']], [100,415])
para('请按 BOM 的完整厂家料号采购。不得将 ESP32-S3-WROOM-1U-N16R8 改为其他 Flash / PSRAM 型号；PAM8302AADCR 为 SO-8，不能换成 ASCR。未知替料须先退回工程确认。')
page();title('装配、坐标与关键工艺')
head('贴装方向')
para('positions.csv 含全部 91 个器件；机器贴装可用 positions-SMT.csv（84 个），回流后直插焊接可用 positions-THT.csv（7 个）。两张分表均直接取自完整原生坐标表，不另行换算。')
para('positions.csv 为 KiCad 原生毫米坐标：原点在板框左上角，X 向右、Y 向上；因此本板多数 Y 为负值。角度为顶视图逆时针方向，零度以原生封装为准，不是某家贴片机的默认零度。请结合顶层装配图和实际 1 脚核对后导入机器。')
para('filled-capped-holes.csv、装配坐标图及接口表使用 PCB 编辑器顶视坐标：同一左上角原点，X 向右、Y 向下。两表的 Y 符号相反，禁止混用。')
head('麦克风与模块')
para('MK1 / MK2 为顶部声孔 MEMS 麦克风。遵照 MSM381A3729H9BPC 厂家说明：回流后禁止整板清洗或刷洗、禁止超声处理、禁止朝声孔吹气或在声孔抽真空、禁止插入异物。采用兼容的免清洗工艺，吸嘴避开声孔。麦克风原厂上限 3 次回流；本板受 U1 模组限制按单次整板回流安排，返修曲线须由工厂评估。')
para('U1 使用外置天线连接器。天线安装在塑料外壳内部，远离电池、扬声器磁体、铜板及人体遮挡；不得金属包覆天线。外壳需给两个麦克风分别留出声道与防风材料，避免扬声器声道直接耦合到麦克风。')
head('降本版与原版区别')
para('替换 17 颗 1% 电阻为国巨 RC0603FR 系列、7 个排针为 XKB；R6/R7 的 0.1% 精密电阻及 R13/R14 保持原料号。主控、双麦、ADC/DAC、功放、电源和固件功能保留。U1 采用分格阻焊，另 7 个焊盘内孔移开，并增大 1 处孔的阻焊间隔，所有散热孔和板级过孔数量保留。ENIG 保留以兼顾 0.4 mm 间距音频芯片焊接平整度。')
head('关键电路识别')
para('C51 / C52 均为 120 pF C0G，合计 240 pF，跨接升压反馈上臂 R4。根据 TPS61023 厂家方程，732 kΩ 与 240 pF 对应约 906 Hz 零点；两颗都要装，不得省略。U5 开关节点需保留原有短宽铜线。')
para('J5 是桥接扬声器输出，SPK_P / SPK_N 两端都不是地；示波器普通接地夹不能夹在任一扬声器端。测试使用差分探头或合适的隔离测量方法。')
head('制造资料使用顺序')
para('制板：Gerber、Excellon 钻孔和本说明。不能沿用 r626 的填孔清单。贴片：bom-grouped.csv、positions.csv、顶层装配图和原厂资料。维修：原理图、KiCad 工程与接口表。文件哈希用于核对传输完整性，不替代板厂工程确认。')
page();title('接口与线束 / 按 1 脚编号')
ports={'J1':['DC5V','GND'],'J2':['BAT','GND'],'J3':['BAT_NTC','GND'],'J4':['VSYS','POWER_EN'],'J5':['SPK_P','SPK_N'],'J6':['GND','UART_TX','UART_RX','RESET','BOOT','D3V3'],'J7':['GND','KEY_MUTE','GND','KEY_UP','GND','KEY_DOWN']}
rows=[['接口','顺序：1 → 最后脚','用途 / 接法']]
notes={'J1':'稳压 5 V ±5%，持续供电 ≥2 A；不是 USB 数据口。','J2':'带保护的 1S 锂电包，满充 4.2 V，持续放电 ≥3 A，充电允许 ≥0.6 A；负载下欠压切断须不低于 3.0 V，不接裸电芯。','J3':'电池表面实装 10 kΩ NTC（25℃）；禁止用固定电阻代替温度检测。','J4':'自锁开关跨接两脚；断开关机，闭合使能。关机时仍可充电。','J5':'4 Ω、额定 ≥3 W 扬声器；短双绞线，禁止任一脚接地。','J6':'3.3 V UART：板 TX 接下载器 RX；板 RX 接下载器 TX。最后脚仅检测电压，禁止外部反供。','J7':'三个常开按钮分别短接 1–2、3–4、5–6：静音、音量加、音量减。'}
for ref,nets in ports.items():
 actual=sorted(g['components'][ref]['pads'],key=lambda p:int(p['pin']));assert [p['net'] for p in actual]==nets
 rows.append([ref,' / '.join(nets),notes[ref]])
table(rows,[35,215,265])
para('排针为 2.54 mm 单排直插，没有机械防反插结构。配套线束必须标出 1 脚，电源正极红线、地黑线，并在外壳端采用有极性的连接方式。电池需按上述截止电压选配或定制保护板，本主板没有独立 3.0 V 欠压切断电路。J1/J2 电源线建议不小于 AWG24、长度不超过 150 mm；扬声器线建议不超过 100 mm。')
rows=[['接口','1 脚 X / Y（mm）','最后脚 X / Y（mm）']]
for ref in ports:
 pads=sorted(g['components'][ref]['pads'],key=lambda p:int(p['pin']));rows.append([ref,f"{pads[0]['x']:.3f} / {pads[0]['y']:.3f}",f"{pads[-1]['x']:.3f} / {pads[-1]['y']:.3f}"])
table(rows,[65,225,225]);para('表中坐标为 PCB 顶视 X 向右、Y 向下。方形焊盘为 1 脚；请以装配图再次核对。')
page();title('首件通电与交付记录')
para('以下是板到手后执行的首件流程，本包不包含已完成的实测记录。先做两台样机；首件确认后再扩大批量。')
steps=[('1. 断电检查','检查极性、分格阻焊开窗及器件方向；测 J1/J2 对地电阻，确认无硬短路。J4 保持断开，扬声器先不连接。'),('2. 限流上电','J1 接 5.0 V 实验电源，初始限流 0.10 A，J2 留空。若持续限流或器件异常升温立即断电；确认 VSYS 工作后再逐步提高限流。'),('3. 检查电源','闭合 J4，分阶段提高电源限流至 2 A。检查 V5 目标约 5 V、D3V3 约 3.32 V、A3V3 约 3.3 V；调试接受窗口分别 4.7–5.3、3.15–3.45、3.23–3.37 V，需记录纹波与启动波形。'),('4. 下载 A / B 固件','用 3.3 V UART；BOOT 拉低时复位进入下载模式。分别按 unit-a / unit-b 的 flasher_args.json 地址烧录 bootloader、分区表、应用及模型分区，不能只烧应用。'),('5. 音频和按键','连接天线、扬声器与三个按钮；从初始低音量开始，核对双向同时说话、回声、噪声、静音与音量调节。两台贴上 A / B 标签。'),('6. 电池与温升','连接符合规格的电池和真实 NTC；测充电、断电切换、低电压截止及最大语音负载下 U4/U5/U6/电池温升。不得用本文件的计算值当实测合格。'),('7. 骑行与距离','先静态近距离，再空旷及城市转弯场景，逐步扩大到 1 公里；记录中断率、语音延迟、风噪和音量。当前没有距离与声学实测保证。')]
table([['步骤','执行内容']]+steps,[95,420])
para('交付：裸板电测结果、AOI / X-ray 记录、实际使用料号、器件替代记录（如有）、首件测量表、两台固件角色及板号。无法满足过孔盖油、细间距或麦克风工艺时，应暂停该工序并反馈，不得自行省略。')
head('所依据的主要原厂资料')
para('TI：BQ24074、TPS61023（第 8.2.2.6 节）、TPSM82821、TLV755P；Espressif：ESP32-S3-WROOM-1U；Everest：ES7210 / ES8311；Diodes：PAM8302A；MEMSensing：MSM381A3729H9BPC（ADDITIONAL NOTES）。确切来源与料号见 BOM 和工程资料。')
def footer(c,doc):
 c.setFont('STSong-Light',8);c.setFillColor(colors.HexColor('#586875'));c.drawString(40,23,'骑行对讲机 / 原型 PCBA / 版本 '+str(s['revision']));c.drawRightString(555,23,str(doc.page))
SimpleDocTemplate(str(out/'制造与装配说明.pdf'),pagesize=(595.28,841.89),rightMargin=40,leftMargin=40,topMargin=38,bottomMargin=40).build(story,onFirstPage=footer,onLaterPages=footer)
for name in ['positions.csv','bom-grouped.csv','bom.csv','bom.json','filled-capped-holes.csv','assembly-geometry.json','assembly-process.json','review_summary.txt','engineering.json']:
 shutil.copy2(review/name,out/name)
(out/'README.txt').write_text('骑行对讲机原型 PCBA\n版本 '+str(s['revision'])+'\n先阅读《制造与装配说明.pdf》。所有文件必须与同包 PCB 对应。\n坐标：positions.csv 为 X右Y上；filled-capped-holes.csv 为 X右Y下。\nGerber 和钻孔仅能由该固定版本通过现有独立人工审阅后导出。\n城市1公里、音量、风噪和温升待实物验证。\n',encoding='utf-8')
print(out/'制造与装配说明.pdf')
