"""External official LTspice runtime and its unchanged installed model library.

No engine or binary model is redistributed. Model packages name and hash files
already in the configured vendor library; caller-supplied binaries are not run.
"""
import hashlib
import json
from io import TextIOWrapper
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
import numpy as np


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def paths():
    value=os.environ.get('PCB_MCP_LTSPICE_ROOT')
    if not value:raise ValueError('LTSPICE_RUNTIME_NOT_CONFIGURED')
    root=Path(value).expanduser().resolve()
    prefix=root/'support/ltspice'
    library=prefix/'drive_c/users/crossover/AppData/Local/LTspice/lib'
    executable=prefix/'drive_c/Program Files/ADI/LTspice/LTspice.exe'
    launcher=root/'bin/wine'
    if not all(p.is_file() for p in (launcher,executable)) or not library.is_dir():
        raise ValueError('LTSPICE_MAC_RUNTIME_LAYOUT_UNAVAILABLE')
    # The official Mac wrapper expects this directory even in batch mode.
    # Do not silently create or redirect any real user's Documents directory.
    if not (prefix/'drive_c/users/crossover/Documents').is_dir():
        raise ValueError('LTSPICE_ISOLATED_DOCUMENTS_DIRECTORY_MISSING')
    return root,prefix,library,executable,launcher


def runtime_identity():
    root,prefix,library,executable,launcher=paths()
    files={}
    for path in [launcher,executable,root/'bin/wineserver',root/'bin/wineloader',
                 root/'lib/wine/x86_64-windows/winewrapper.exe',
                 root/'lib/wine/x86_64-unix/ntdll.so']:
        if path.is_file():files[str(path.relative_to(root))]=sha(path)
    for path in sorted(library.rglob('*')):
        if path.is_file():files[str(path.relative_to(root))]=sha(path)
    return dict(dialect='ltspice',root=str(root),files=files,
                scope='configured official Mac runtime binaries and complete vendor model/symbol library; operating system excluded')


def library_file(library,relative):
    item=PurePosixPath(relative)
    if (item.is_absolute() or '..' in item.parts or str(item)!=relative or
        not re.fullmatch(r'[A-Za-z0-9_./+ -]{1,240}',relative)):
        raise ValueError('LTSPICE_LIBRARY_PATH_INVALID')
    path=library.joinpath(*item.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(library.resolve()) or not path.is_file():
        raise ValueError('LTSPICE_LIBRARY_FILE_UNAVAILABLE')
    return path


def resolve_model(spec,name,pins):
    _,_,library,_,_=paths()
    model=library_file(library,spec['model_relative_path'])
    symbol=library_file(library,spec['symbol_relative_path'])
    if sha(model)!=spec['model_sha256'] or sha(symbol)!=spec['symbol_sha256']:
        raise ValueError('LTSPICE_VENDOR_MODEL_CHANGED')
    header=model.read_bytes().lstrip(b'\r\n')
    if not header.startswith((b'<Binary File>',b'* LTspice Encrypted File')):
        raise ValueError('LTSPICE_EXTERNAL_BINARY_FORMAT_REQUIRED')
    text=symbol.read_text(encoding='utf-8')
    def attribute(key,optional=False):
        values=re.findall(r'^SYMATTR '+key+r' (.+)$',text,re.M)
        if not values and optional:return None
        if len(values)!=1:raise ValueError('LTSPICE_SYMBOL_ATTRIBUTE_MISSING')
        return values[0].strip()
    order=re.findall(r'^PINATTR SpiceOrder (\d+)\s*$',text,re.M)
    ports=[];current=None
    for line in text.splitlines():
        if line.startswith('PIN '):current={};ports.append(current)
        elif line.startswith('PINATTR ') and current is not None:
            _,key,value=line.split(' ',2)
            if key in current:raise ValueError('LTSPICE_VENDOR_PORT_ATTRIBUTE_DUPLICATE')
            current[key]=value.strip()
    entry=attribute('SpiceModel',optional=True) or attribute('Value')
    filename=attribute('ModelFile')
    expected=spec['model_relative_path'].removeprefix('sub/') if '/' in filename else model.name
    if entry!=name or filename!=expected:
        raise ValueError('LTSPICE_SYMBOL_MODEL_IDENTITY_MISMATCH')
    if len(ports)!=len(pins) or len(order)!=len(pins) or sorted(map(int,order))!=list(range(1,len(pins)+1)):
        raise ValueError('LTSPICE_VENDOR_PORT_COUNT_MISMATCH')
    if all(p.get('PinName','').isdigit() for p in ports):
        actual=[p['PinName'] for p in sorted(ports,key=lambda p:int(p['SpiceOrder']))]
        if actual!=pins:raise ValueError('LTSPICE_VENDOR_PHYSICAL_PIN_ORDER_MISMATCH')
    return model


def read_ascii_raw(path):
    if path.stat().st_size > 160_000_000:
        raise ValueError('LTSPICE_RAW_SIZE_LIMIT')
    with path.open('rb') as raw:
        encoding = 'utf-16' if raw.read(2) in (b'\xff\xfe', b'\xfe\xff') else 'utf-8'
        raw.seek(0)
        with TextIOWrapper(raw, encoding=encoding) as stream:
            header_rows = []
            for _ in range(1100):
                line = stream.readline(4097)
                if line == 'Values:\n':
                    break
                if not line or len(line) > 4096:
                    raise ValueError('LTSPICE_ASCII_RAW_REQUIRED')
                header_rows.append(line)
            else:
                raise ValueError('LTSPICE_ASCII_RAW_REQUIRED')
            header = ''.join(header_rows)

            def field(key):
                rows = re.findall(r'^' + re.escape(key) + r':\s*(.*?)\s*$', header, re.M)
                if len(rows) != 1:
                    raise ValueError('LTSPICE_RAW_HEADER_INVALID')
                return rows[0]

            count = int(field('No. Variables'))
            points = int(field('No. Points'))
            if not 1 <= count <= 1000 or not 1 <= points <= 500000 or count * points > 8_000_000:
                raise ValueError('LTSPICE_RAW_DIMENSIONS_INVALID')
            flags = field('Flags').split()
            complex_values = 'complex' in flags
            if ('real' in flags) == complex_values:
                raise ValueError('LTSPICE_RAW_FLAGS_INVALID')
            variables = header.split('Variables:\n')
            if len(variables) != 2:
                raise ValueError('LTSPICE_RAW_VARIABLES_INVALID')
            names = []
            for index, line in enumerate(variables[1].strip().splitlines()):
                cells = line.split()
                if len(cells) != 3 or int(cells[0]) != index:
                    raise ValueError('LTSPICE_RAW_VARIABLE_INDEX_INVALID')
                names.append(cells[1].lower())
            if len(names) != count or len(set(names)) != count:
                raise ValueError('LTSPICE_RAW_VARIABLE_COUNT_INVALID')

            data = np.empty((points, count), dtype=complex if complex_values else float)
            readline = stream.readline
            first = readline(4097)
            while first and not first.strip():
                first = readline(4097)
            for index, row in enumerate(data):
                for column in range(count):
                    line = first if index == column == 0 else readline(4097)
                    if not line:
                        raise ValueError('LTSPICE_RAW_INCOMPLETE')
                    cells = line.split()
                    if len(line) > 4096 or len(cells) != (2 if column == 0 else 1):
                        raise ValueError('LTSPICE_RAW_POINT_INDEX_INVALID')
                    if column == 0 and int(cells[0]) != index:
                        raise ValueError('LTSPICE_RAW_POINT_INDEX_INVALID')
                    value = cells[-1]
                    if complex_values:
                        pair = value.split(',')
                        if len(pair) != 2:
                            raise ValueError('LTSPICE_RAW_COMPLEX_INVALID')
                        row[column] = complex(*map(float, pair))
                    else:
                        row[column] = float(value)
            while tail := readline(4097):
                if tail.strip():
                    raise ValueError('LTSPICE_RAW_INCOMPLETE')
    if not np.all(np.isfinite(data)):
        raise ValueError('LTSPICE_RAW_NONFINITE')
    return dict(zip(names,data.T)),dict(plot=field('Plotname'),points=points,variables=count)


@contextmanager
def short_workspace(folder):
    # The Mac vendor wrapper still limits Windows-style input paths. Keep its
    # working files short even when the retained project path is long/Unicode.
    with tempfile.TemporaryDirectory(prefix='pcb-ltspice-',dir='/private/tmp') as directory:
        stage=Path(directory)
        for path in [folder/'circuit.cir',*folder.glob('vendor_*.sub')]:shutil.copyfile(path,stage/path.name)
        try:yield stage
        finally:
            for name in ('circuit.log','circuit.raw','engine.log'):
                path=stage/name
                if path.is_file():shutil.copyfile(path,folder/name)


def diagnostic(path):
    if not path.is_file():return 'no vendor log'
    raw=path.read_bytes()[-12000:]
    encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else ('utf-16-le' if b'\x00' in raw[:100] else 'utf-8')
    return raw.decode(encoding,errors='replace')[-1500:]


def stop_timeout(proc,marker):
    """Reap wrappers and stop only exact-deck broker children after a timeout."""
    errors=[];stdout=stderr=''
    for sig in (signal.SIGTERM,signal.SIGKILL):
        if proc.poll() is None:
            try:os.killpg(proc.pid,sig)
            except ProcessLookupError:pass
            except PermissionError as exc:
                if proc.poll() is None:errors.append(repr(exc))
        try:
            stdout,stderr=proc.communicate(timeout=.2)
            break
        except subprocess.TimeoutExpired:pass
    try:
        from pcb_mcp_client.process_job import stop_marked
        cleanup=stop_marked(marker,grace_s=.1)
    except Exception as exc:cleanup=dict(verified_stopped=False,error=repr(exc))
    try:stdout,stderr=proc.communicate(timeout=.2)
    except subprocess.TimeoutExpired:
        cleanup.update(verified_stopped=False,wrapper_or_pipe_survived=True)
        proc.stdout.close();proc.stderr.close()
    cleanup['group_errors']=errors
    if proc.poll() is None:cleanup['verified_stopped']=False
    return stdout,stderr,cleanup


def run(deck,vectors,folder,timeout=60,vendor_models=None):
    from .spice import runtime_hash
    identity=runtime_identity();root,_,_,_,launcher=paths()
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    for name,path in (vendor_models or {}).items():
        if not re.fullmatch(r'vendor_[a-f0-9]{64}\.sub',name):raise ValueError('LTSPICE_STAGE_NAME_INVALID')
        if sha(Path(path))!=name[7:-4]:raise ValueError('LTSPICE_VENDOR_MODEL_CHANGED')
        shutil.copyfile(path,folder/name)
    # .save is generated from resolved nets and instrument probes, never from
    # an unrestricted caller control block. Keep the complete requested set.
    if not all(re.fullmatch(r'(?:v\([a-z0-9_]+\)|i\(v[a-z0-9_]+\)|i\((?:cfixture|[rc])[0-9]+\)|time|frequency)',v) for v in vectors):
        raise ValueError('LTSPICE_VECTOR_INVALID')
    if not deck.endswith('.end\n'):raise ValueError('LTSPICE_DECK_END_REQUIRED')
    # Small analog boards suffer substantial synchronization overhead with the
    # host-wide default thread count. This affects execution, not device data,
    # accuracy settings, maximum timestep or the declared observation windows.
    actual=deck[:-5]+'.options plotwinsize=0 numdgt=15 threads=1\n.save '+' '.join(v for v in vectors if v not in ('time','frequency'))+'\n.end\n'
    (folder/'circuit.cir').write_text(actual)
    (folder/'input.json').write_text(json.dumps(dict(deck=actual,vectors=vectors,runtime=identity),indent=2))
    def windows(path):return 'Z:'+str(path).replace('/','\\')
    with short_workspace(folder) as stage:
        command=[str(launcher),'--cx-log',str(stage/'engine.log'),'--wait-children','--bottle=ltspice','--scope=managed',
                 '--workdir',windows(stage),'C:\\Program Files\\ADI\\LTspice\\LTspice.exe','-b','-ascii','circuit.cir']
        start=time.monotonic()
        proc=subprocess.Popen(command,cwd=stage,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,
                              env={**os.environ,'WINEDEBUG':'-all','MVK_CONFIG_LOG_LEVEL':'0'})
        try:
            stdout,stderr=proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            stdout,stderr,cleanup=stop_timeout(proc,str(stage/'circuit.cir'))
            (folder/'process.json').write_text(json.dumps(dict(status='timeout',timeout_s=timeout,command=command,elapsed_seconds=time.monotonic()-start,stdout=stdout,stderr=stderr,cleanup=cleanup)))
            raise ValueError('LTSPICE_TIMEOUT'+('' if cleanup['verified_stopped'] else '_CLEANUP_UNVERIFIED')) from error
        (folder/'process.json').write_text(json.dumps(dict(command=command,returncode=proc.returncode,stdout=stdout,stderr=stderr,elapsed_seconds=time.monotonic()-start,retained_directory=str(folder)),indent=2))
    if proc.returncode or not all((folder/name).is_file() for name in ('circuit.log','circuit.raw')):
        if 'wineserver: bind: Operation not permitted' in diagnostic(folder/'engine.log'):
            raise ValueError('LTSPICE_RUNTIME_SOCKET_PERMISSION_DENIED')
        raise ValueError('LTSPICE_EXECUTION_OR_OUTPUT_FAILED: '+diagnostic(folder/'circuit.log'))
    waves,meta=read_ascii_raw(folder/'circuit.raw')
    if set(vectors)-set(waves):raise ValueError('LTSPICE_REQUESTED_VECTOR_MISSING')
    if runtime_identity()!=identity:raise ValueError('LTSPICE_RUNTIME_CHANGED')
    result=dict(status='pass',dialect='ltspice',runtime_sha256=runtime_hash(identity),raw=meta,
                deck_sha256=sha(folder/'circuit.cir'),log_sha256=sha(folder/'circuit.log'),raw_sha256=sha(folder/'circuit.raw'))
    (folder/'solver.json').write_text(json.dumps(result,indent=2));np.savez(folder/'vectors.npz',**{v:waves[v] for v in vectors})
    return result,{v:waves[v] for v in vectors}
