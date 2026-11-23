"""Isolated shared-ngspice worker. Only receives server-generated circuit data."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import sys
import numpy as np


class Complex(C.Structure):
    _fields_=[('real',C.c_double),('imag',C.c_double)]


class Vector(C.Structure):
    _fields_=[('name',C.c_char_p),('kind',C.c_int),('flags',C.c_short),('real',C.POINTER(C.c_double)),('complex',C.POINTER(Complex)),('length',C.c_int)]


def run(folder,library):
    job=json.loads((folder/'input.json').read_text());messages=[];exits=[]
    # Some vendor parser failures terminate the shared library process before
    # returning to Python. Retain callback diagnostics as they arrive.
    log=(folder/'solver.log').open('w')
    char=C.CFUNCTYPE(C.c_int,C.c_char_p,C.c_int,C.c_void_p)
    exitcb=C.CFUNCTYPE(C.c_int,C.c_int,C.c_bool,C.c_bool,C.c_int,C.c_void_p)
    @char
    def output(value,ident,user):
        line=value.decode(errors='replace');messages.append(line);log.write(line+'\n');log.flush();return 0
    @exitcb
    def terminated(code,immediate,quit,ident,user):exits.append(code);return 0
    lib=C.CDLL(str(library))
    # Parent supplies empty local startup files and isolated script directories.
    # KiCad's bundled library crashes in ngSpice_nospinit before Init (its
    # variable table is not initialized); do not call that broken API here.
    lib.ngSpice_Init.argtypes=[char,char,exitcb,C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p]
    lib.ngSpice_Init(output,output,terminated,None,None,None,None)
    lib.ngSpice_Circ.argtypes=[C.POINTER(C.c_char_p)];lib.ngSpice_Circ.restype=C.c_int
    lib.ngSpice_Command.argtypes=[C.c_char_p];lib.ngSpice_Command.restype=C.c_int
    lib.ngGet_Vec_Info.argtypes=[C.c_char_p];lib.ngGet_Vec_Info.restype=C.POINTER(Vector)
    dialect=job.get('dialect','native')
    if dialect not in ('native','pspice'):raise ValueError('SPICE_DIALECT_UNSUPPORTED')
    for cmd in (b'version',b'set noaskquit',b'set num_threads=1'):lib.ngSpice_Command(cmd)
    identity=job['runtime']
    if str(library)!=identity['library'] or hashlib.sha256(library.read_bytes()).hexdigest()!=identity['library_sha256']:
        raise ValueError('NGSPICE_LIBRARY_CHANGED')
    setup_codes=[]
    if dialect=='pspice':
        # Models are inlined, so the documented whole-deck mode psa is needed.
        setup_codes.append(lib.ngSpice_Command(b'set ngbehavior=psa'))
        for filename,expected in identity['code_models'].items():
            path=Path(filename)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('NGSPICE_CODEMODEL_CHANGED')
            local=folder/'code_models'/path.name
            if hashlib.sha256(local.read_bytes()).hexdigest()!=expected:raise ValueError('NGSPICE_STAGED_CODEMODEL_CHANGED')
            # ngSpice_Command's codemodel handler treats quotes as filename
            # bytes. A server-created relative path also works when the run
            # directory or original application directory contains spaces.
            setup_codes.append(lib.ngSpice_Command(('codemodel code_models/'+path.name).encode()))
    lines=[line.encode() for line in job['deck'].splitlines()]
    array=(C.c_char_p*(len(lines)+1))(*lines,None)
    codes=setup_codes+[lib.ngSpice_Circ(array),lib.ngSpice_Command(b'run')]
    vectors={}
    for name in job['vectors']:
        ptr=lib.ngGet_Vec_Info(name.encode())
        if not ptr:continue
        v=ptr.contents
        if not 0<v.length<=2_000_000:continue
        if v.real:data=np.ctypeslib.as_array(v.real,shape=(v.length,)).copy()
        elif v.complex:data=np.array([complex(v.complex[i].real,v.complex[i].imag) for i in range(v.length)])
        else:continue
        if np.all(np.isfinite(data)):vectors[name]=data
    log.close()
    np.savez_compressed(folder/'vectors.npz',**vectors)
    errors=[line for line in messages if any(token in line.lower() for token in
            ('error:', 'fatal:', 'timestep too small', 'unknown parameter', 'unrecognized parameter', 'model type mismatch', 'simulation interrupted'))]
    result=dict(status='pass' if set(vectors)==set(job['vectors']) and not any(codes) and not exits and not errors else 'fail',dialect=dialect,runtime_sha256=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest(),code_models=identity['code_models'],commands=codes,controlled_exits=exits,errors=errors,vector_lengths={n:len(v) for n,v in vectors.items()},library=str(library),library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),deck_sha256=hashlib.sha256(job['deck'].encode()).hexdigest())
    (folder/'solver.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    result=run(Path(sys.argv[1]),Path(sys.argv[2]));raise SystemExit(0 if result['status']=='pass' else 1)
