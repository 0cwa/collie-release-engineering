"""Verify the recorded NDK r28 payload tree; this is not complete host provenance."""
from __future__ import annotations
import hashlib,json,os,stat,xml.etree.ElementTree as ET
from pathlib import Path

class Failure(Exception): pass

def require(value,message):
    if not value: raise Failure(message)

def digest_records(entries):
    h=hashlib.sha256()
    for entry in sorted(entries,key=lambda e:e['path']):
        h.update(json.dumps(entry,sort_keys=True,separators=(',',':')).encode()+b'\n')
    return h.hexdigest()

def verify(ndk, expected_digest, expected_count):
    ndk=ndk.resolve(strict=True);require(ndk.is_dir(),'NDK root missing')
    metadata=ndk/'package.xml'
    if os.path.lexists(metadata):
        st=metadata.lstat();require(stat.S_ISREG(st.st_mode) and st.st_size<=1024*1024 and not st.st_mode & 0o022,'NDK package metadata invalid')
        try:
            root=ET.fromstring(metadata.read_bytes())
        except ET.ParseError as exc:
            raise Failure('NDK package metadata is malformed') from exc
        elements=[e for e in root.iter() if e.tag.split('}')[-1]=='localPackage']
        require(len(elements)==1 and elements[0].get('path')=='ndk;28.0.13004108','NDK package metadata version differs')
    entries=[]
    def walk_error(error): raise Failure('Cannot enumerate NDK installation: '+str(error))
    for directory,dirs,files in os.walk(ndk,followlinks=False,onerror=walk_error):
        ds=Path(directory).lstat();require(stat.S_ISDIR(ds.st_mode) and bool(ds.st_mode & stat.S_IRUSR) and bool(ds.st_mode & stat.S_IXUSR) and not ds.st_mode & (stat.S_ISUID|stat.S_ISGID|stat.S_ISVTX|0o022),'NDK directory mode invalid')
        for name in list(dirs):
            path=Path(directory)/name
            if path.is_symlink():dirs.remove(name);files.append(name)
        for name in files:
            path=Path(directory)/name;rel=path.relative_to(ndk).as_posix()
            if rel=='package.xml':continue
            st=path.lstat()
            if stat.S_ISLNK(st.st_mode):
                require(path.exists() and path.resolve().is_relative_to(ndk), 'NDK symlink target missing or escapes root')
                entries.append({'path':rel,'type':'symlink','target':os.readlink(path)});continue
            require(stat.S_ISREG(st.st_mode) and bool(st.st_mode & stat.S_IRUSR) and not st.st_mode & (stat.S_ISUID|stat.S_ISGID|stat.S_ISVTX|0o022) and (bool(st.st_mode & stat.S_IXUSR) or not st.st_mode & 0o111),'NDK payload file mode invalid')
            h=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
            entries.append({'path':rel,'type':'file','sha256':h.hexdigest(),'executable':bool(st.st_mode & stat.S_IXUSR)})
    require(len(entries)==expected_count and digest_records(entries)==expected_digest,'NDK payload tree differs')
    return len(entries)
