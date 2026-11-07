"""Verify recorded Rust component payloads, not the complete host toolchain."""
from __future__ import annotations
import hashlib,json,os,re,stat
from pathlib import Path,PurePosixPath

class Failure(Exception): pass

def require(value,message):
    if not value: raise Failure(message)

def verify(toolchain,reference,reference_hash):
    toolchain=toolchain.resolve(strict=True)
    require(reference.is_file() and not reference.is_symlink() and reference.stat().st_size<=1024*1024,'Rust reference invalid')
    raw=reference.read_bytes();require(hashlib.sha256(raw).hexdigest()==reference_hash,'Rust reference digest differs')
    data=json.loads(raw);entries=data['entries'];require(isinstance(entries,list) and entries,'Rust reference is empty')
    seen=set()
    for item in entries:
        require(item.get('type')=='file' and set(item)=={'component','path','type','sha256','executable'},'Unexpected Rust payload entry')
        name=item['path'];require(isinstance(name,str) and name,'Empty Rust payload path');relative=PurePosixPath(name)
        require(not relative.is_absolute() and name==relative.as_posix() and all(x not in ('.','..') for x in relative.parts) and name not in seen,'Unsafe or duplicate Rust payload path')
        require(isinstance(item['sha256'],str) and re.fullmatch('[a-f0-9]{64}',item['sha256']) and isinstance(item['executable'],bool),'Invalid Rust payload hash or mode')
        seen.add(name);path=toolchain.joinpath(*relative.parts)
        require(path.parent.resolve()==path.parent and path.parent.is_relative_to(toolchain),'Rust payload parent invalid')
        st=path.lstat();require(stat.S_ISREG(st.st_mode) and bool(st.st_mode & stat.S_IRUSR) and not st.st_mode & (stat.S_ISUID|stat.S_ISGID|stat.S_ISVTX|0o022) and bool(st.st_mode & stat.S_IXUSR)==item['executable'] and (item['executable'] or not st.st_mode & 0o111),'Rust payload mode differs')
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        require(h.hexdigest()==item['sha256'],'Rust payload bytes differ: '+name)
    return len(seen)
