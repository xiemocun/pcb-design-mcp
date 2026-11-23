"""Restricted SPICE data adapter; no caller control language or file commands."""
import json
import math
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import shutil
import numpy as np


def model_text(raw):
    try:return raw.decode('utf-8')
    except UnicodeDecodeError:
        # Some official models use a Windows dash/copyright byte in comments.
        # Preserve their bytes/hash; only permit this fallback when all model
        # cards themselves remain ASCII. No ambiguous executable text decoding.
        text=raw.decode('latin-1')
        if any(not line.isascii() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('*')):
            raise ValueError('SPICE_NONASCII_MODEL_CARD')
        return text


def model_lines(text,dialect='native'):
    """Permit numeric model/subcircuit data, not simulator commands or file I/O.

    Expressions in vendor models are allowed; command substitutions, file paths,
    external code models, behavioral file readers and control blocks are not.
    Supported primitive cards are intentionally separate from arbitrary code.
    """
    if dialect not in ('native','pspice','ltspice'):raise ValueError('SPICE_DIALECT_UNSUPPORTED')
    if len(text)>4_000_000 or '\x00' in text:raise ValueError('SPICE_MODEL_SIZE_OR_NUL')
    lines=[];depth=0
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith('*'):continue
        checked=line
        if line.lower().startswith('.subckt ') or line[0].lower() in ('x','+'):
            checked=re.sub(r'\bparams:', 'params',checked,flags=re.I)
        # PSpice uses semicolons only as end-of-card comments. They cannot
        # introduce executable cards here; retain the original asset unchanged.
        if dialect in ('pspice','ltspice') and ';' in checked:
            comment=checked.split(';',1)[1]
            if re.search(r'\b(?:pwlfile|file|load|shell|source|include|codemodel|osdi)\b',comment,re.I) or any(c in comment for c in ('`','\\','"',"'",'$')):
                raise ValueError('SPICE_MODEL_UNSAFE_COMMENT')
            line=line.split(';',1)[0].rstrip();checked=checked.split(';',1)[0].rstrip()
            if not line:continue
        if any(x in checked for x in ('`',';','\\','"',"'",'$',':')):raise ValueError('SPICE_MODEL_UNSAFE_TOKEN')
        if dialect=='pspice' and '^' in line:raise ValueError('PSPICE_XOR_REQUIRES_VERIFIED_ADAPTER')
        if re.search(r'\b(?:pwlfile|file|shell|source|include|codemodel|osdi)\b',line,re.I) or re.search(r'\bload\s*\(|^\.?load\s',line,re.I):raise ValueError('SPICE_MODEL_FILE_OR_CODE_REFERENCE')
        token=line.split()[0].lower()
        if token.startswith('.'):
            if token not in ('.model','.subckt','.ends','.param','.func','.ic'):raise ValueError('SPICE_MODEL_DIRECTIVE_NOT_ALLOWED: '+token)
            if token=='.ic':
                number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[a-zA-Z]*'
                value=r'(?:'+number+r'|\{[A-Za-z_][A-Za-z0-9_]*\})'
                assignment=r'\s+[vV]\([A-Za-z0-9_+-]+\)\s*=?\s*'+value
                if depth<1 or not re.fullmatch(r'(?i:\.ic)(?:'+assignment+r')+',line):raise ValueError('SPICE_MODEL_INTERNAL_IC_REQUIRED')
            if token=='.subckt':depth+=1
            if token=='.ends':depth-=1
            if depth<0:raise ValueError('SPICE_SUBCKT_UNBALANCED')
        # T is the built-in lossless transmission line, also used for vendor
        # comparator propagation delays. It is model data, not a command or an
        # external code model; all token/file/directive restrictions above apply.
        elif not token.startswith('+') and token[0].lower() not in 'rcldvimefgbhqjxkswt':raise ValueError('SPICE_MODEL_CARD_NOT_ALLOWED')
        lines.append(line)
    if depth:raise ValueError('SPICE_SUBCKT_UNBALANCED')
    return lines


def runtime_identity(dialect='native'):
    if dialect=='ltspice':
        from . import ltspice
        return ltspice.runtime_identity()
    if dialect not in ('native','pspice'):raise ValueError('SPICE_DIALECT_UNSUPPORTED')
    library=Path(os.environ.get('PCB_MCP_NGSPICE_LIBRARY','/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib')).expanduser().resolve()
    if not library.is_file():raise ValueError('NGSPICE_LIBRARY_UNAVAILABLE')
    models={}
    if dialect=='pspice':
        directory=Path(os.environ.get('PCB_MCP_NGSPICE_CODEMODEL_DIR',str(library.parent.parent/'PlugIns/sim/ngspice'))).expanduser().resolve()
        for name in ('spice2poly.cm','analog.cm','digital.cm','xtradev.cm','xtraevt.cm','table.cm','tlines.cm'):
            # Only fixed standard modules from the configured engine location
            # may be loaded; the caller's model assets cannot supply binaries.
            path=directory/name
            if path.is_file():models[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        if not all(str(directory/name) in models for name in ('analog.cm','spice2poly.cm')):
            raise ValueError('NGSPICE_STANDARD_CODEMODELS_UNAVAILABLE')
    return dict(library=str(library),library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),dialect=dialect,code_models=models)


def runtime_hash(identity):
    return hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class SimulationFailure(ValueError):
    """Carry a stable finding code and the evidence needed to interpret it."""
    def __init__(self, code, message, next_action, **details):
        super().__init__(code)
        self.finding = dict(code=code, message=message, next_action=next_action, **details)


def timestep_diagnostic(directory, deck, runtime):
    """Accept only a standalone transient convergence failure from this deck and runtime."""
    try:
        report = json.loads((directory / 'solver.json').read_text())
        process = json.loads((directory / 'process.json').read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or not isinstance(process, dict):
        return None
    commands, errors = report.get('commands'), report.get('errors')
    valid = (process.get('returncode') == 1 and report.get('status') == 'fail'
             and report.get('runtime_sha256') == runtime
             and report.get('deck_sha256') == hashlib.sha256(deck.encode()).hexdigest()
             and report.get('controlled_exits') == [] and isinstance(commands, list)
             and bool(commands) and all(code == 0 for code in commands)
             and isinstance(errors, list) and bool(errors)
             and all(isinstance(line, str) and line.split()[:6] ==
                     ['stderr', 'doAnalyses:', 'TRAN:', 'Timestep', 'too', 'small;'] for line in errors))
    if not valid:
        return None
    detail = {}
    match = re.search(r'\btime\s*=\s*([0-9.eE+-]+)\s*,', errors[-1])
    if match:
        try:
            stop = float(match[1])
        except ValueError:
            pass
        else:
            if math.isfinite(stop) and stop >= 0:
                detail['solver_stop_s'] = stop
    return detail


def run(deck,vectors,folder,timeout=60,dialect='native',vendor_models=None):
    if dialect=='ltspice':
        from . import ltspice
        return ltspice.run(deck,vectors,folder,timeout,vendor_models)
    if vendor_models:raise ValueError('VENDOR_BINARY_REQUIRES_QUALIFIED_LTSPICE_RUNTIME')
    if dialect not in ('native','pspice'):raise ValueError('SPICE_DIALECT_UNSUPPORTED')
    identity=runtime_identity(dialect)
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    (folder/'input.json').write_text(json.dumps(dict(deck=deck,vectors=vectors,dialect=dialect,runtime=identity),indent=2)+'\n')
    if identity['code_models']:
        local=folder/'code_models';local.mkdir()
        for filename in identity['code_models']:shutil.copyfile(filename,local/Path(filename).name)
    (folder/'circuit.cir').write_text(deck)
    (folder/'.spiceinit').write_text('* Generated empty user initialization\n')
    scripts=folder/'scripts';scripts.mkdir()
    (scripts/'spinit').write_text('* Generated empty system initialization\n')
    library=Path(identity['library'])
    try:
        proc=subprocess.run([sys.executable,str(Path(__file__).with_name('spice_worker.py')),str(folder),str(library)],cwd=folder,env={**os.environ,'SPICE_LIB_DIR':str(folder),'SPICE_SCRIPTS':str(scripts),'SPICE_USERINIT_DIR':str(folder)},capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        (folder/'process.json').write_text(json.dumps(dict(status='timeout',timeout_s=timeout)))
        raise ValueError('NGSPICE_TIMEOUT') from exc
    (folder/'process.json').write_text(json.dumps(dict(returncode=proc.returncode,stdout=proc.stdout,stderr=proc.stderr),indent=2)+'\n')
    if proc.returncode or not (folder/'solver.json').is_file():
        files = {name: str(folder / name) for name in ('solver.log', 'solver.json', 'process.json')
                 if (folder / name).is_file()}
        detail = timestep_diagnostic(folder, deck, runtime_hash(identity))
        if detail is not None:
            when = f"在 {detail['solver_stop_s']:.6g} 秒时" if 'solver_stop_s' in detail else ''
            raise SimulationFailure('NGSPICE_TRANSIENT_TIMESTEP_TOO_SMALL',
                f'瞬态求解{when}因时间步过小中断，该场景缺少完整波形。',
                '核对中断时的供电、负载和器件模型适用范围，再复跑原测量窗口。', solver_files=files, **detail)
        raise SimulationFailure('NGSPICE_EXECUTION_FAILED', '求解器执行失败，该场景缺少有效波形。',
                                '查看本场景的求解日志和进程回执，定位执行错误后复跑。', solver_files=files)
    result=json.loads((folder/'solver.json').read_text())
    if result.get('runtime_sha256')!=runtime_hash(identity) or runtime_identity(dialect)!=identity:raise ValueError('NGSPICE_RUNTIME_CHANGED')
    if result['status']!='pass':raise ValueError('NGSPICE_MISSING_OR_INVALID_RESULTS')
    with np.load(folder/'vectors.npz',allow_pickle=False) as data:values={key:data[key].copy() for key in data.files}
    return result,values
