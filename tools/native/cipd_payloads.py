"""Verify recorded CIPD deployment payloads; this is not complete build provenance."""
from __future__ import annotations
import base64,hashlib,json,os,re,stat
from pathlib import Path,PurePosixPath

SUPPLEMENTAL = {
    'src/third_party/jdk11/.gitignore': 'dbfaef22e39c0352a78e8850f3f457ffff760816877859a29c6881ce32cfbd50',
    'src/third_party/jdk11/BUILD.gn': '45ab43cc4035396ae12f911bc7843929932cdb231be3545afe8d82ead0509488',
    'src/third_party/jdk11/OWNERS': '2192ecef31bb1180388be00928b26c80d6a7162e9abe96f11aec4d39227d079e',
    'src/third_party/jdk11/README.chromium': '22ed1f0e28fd27a19d5c7f9ca77e77e3cef7aa41b047078acdcebd44e6c78f46',
}

class Failure(Exception): pass

def require(value,message):
    if not value: raise Failure(message)

def safe_rel(name):
    require(isinstance(name,str) and name, 'Empty CIPD path')
    p=PurePosixPath(name)
    require(not p.is_absolute() and name==p.as_posix() and all(x not in ('.','..') for x in p.parts),'Unsafe CIPD path')
    return p

def read_regular(path,limit):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=limit, 'Missing or invalid CIPD metadata')
    return path.read_bytes()

def verify(workspace,reference,reference_hash):
    workspace=workspace.resolve(strict=True)
    data=read_regular(reference,1024*1024)
    require(hashlib.sha256(data).hexdigest()==reference_hash,'CIPD reference digest differs')
    ref=json.loads(data); require(ref.get('schema')==1 and isinstance(ref.get('packages'),list) and ref['packages'],'Invalid CIPD reference')
    expected={}
    for row in ref['packages']:
        require(set(row)=={'subdir','package','instance','manifest_sha256','method'}, 'Invalid CIPD reference row')
        sub=safe_rel(row['subdir']);require(sub.parts[0]=='src', 'CIPD package root must be under src')
        require(isinstance(row['package'],str) and re.fullmatch(r'[A-Za-z0-9_./+\-]+',row['package']) and isinstance(row['instance'],str) and re.fullmatch(r'(?:[A-Za-z0-9_-]{43}C|[a-f0-9]{40})',row['instance']) and isinstance(row['manifest_sha256'],str) and re.fullmatch('[a-f0-9]{64}',row['manifest_sha256']) and row['method'] in ('reconstructed-archive','verified-original-archive'), 'Invalid CIPD pin')
        key=(row['subdir'],row['package'])
        require(key not in expected, 'Duplicate CIPD package reference');expected[key]=row
    packages=workspace/'.cipd/pkgs';require(packages.is_dir() and packages.resolve()==packages,'CIPD deployment metadata missing')
    seen=set(); entries=0; roots=set(); allowed_files=set(); allowed_dirs=set()
    for pkg in packages.iterdir():
        require(pkg.name.isdigit() and pkg.is_dir() and not pkg.is_symlink(),'Unexpected CIPD package metadata')
        desc=json.loads(read_regular(pkg/'description.json',4096));key=(desc.get('subdir'),desc.get('package_name'))
        require(key in expected and key not in seen,'Unexpected or duplicate installed CIPD package');seen.add(key);row=expected[key]
        current=pkg/'_current';require(current.is_symlink() and os.readlink(current)==row['instance'],'CIPD instance differs')
        instance=pkg/row['instance'];require(instance.is_dir() and not instance.is_symlink(),'CIPD instance directory invalid')
        metadata_dir=instance/'.cipdpkg';require(metadata_dir.is_dir() and not metadata_dir.is_symlink(),'CIPD metadata directory invalid')
        metadata=metadata_dir/'manifest.json';raw=read_regular(metadata,16*1024*1024)
        require(hashlib.sha256(raw).hexdigest()==row['manifest_sha256'],'CIPD manifest digest differs')
        manifest=json.loads(raw);require(manifest.get('package_name')==row['package'] and isinstance(manifest.get('files'),list),'CIPD manifest invalid')
        root=workspace.joinpath(*safe_rel(row['subdir']).parts)
        require(root.resolve()==root and root.is_dir(),'CIPD installation root invalid')
        roots.add(root)
        names=set()
        for entry in manifest['files']:
            path_name=entry['name'];rel=safe_rel(path_name);require(path_name not in names,'Duplicate CIPD entry');names.add(path_name)
            path=root.joinpath(*rel.parts);require(path.parent.resolve()==path.parent and path.parent.is_relative_to(root),'CIPD entry parent invalid')
            allowed_files.add(path)
            st=path.lstat();entries+=1
            if 'symlink' in entry:
                require(stat.S_ISLNK(st.st_mode) and os.readlink(path)==entry['symlink'],'CIPD symlink differs')
                continue
            require(stat.S_ISREG(st.st_mode) and bool(st.st_mode & stat.S_IRUSR) and st.st_size==entry['size'] and not (st.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX | 0o222)) and bool(st.st_mode & stat.S_IXUSR)==bool(entry.get('executable',False)) and (bool(entry.get('executable',False)) or not st.st_mode & 0o111),'CIPD file type, size, or mode differs')
            h=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
            actual=base64.urlsafe_b64encode(h.digest()+b'\x02').decode().rstrip('=')
            require(actual==entry['hash'],'CIPD file bytes differ')
        vf=manifest.get('version_file')
        if vf:
            require(vf in names,'CIPD version file missing from manifest')
            actual=json.loads(read_regular(root.joinpath(*safe_rel(vf).parts),4096))
            require(actual=={'package_name':row['package'],'instance_id':row['instance']},'CIPD generated version file differs')
    require(seen==set(expected),'Missing CIPD packages')
    for name,digest in SUPPLEMENTAL.items():
        path=workspace.joinpath(*safe_rel(name).parts)
        if not any(path.is_relative_to(root) for root in roots):
            continue
        st=path.lstat();require(stat.S_ISREG(st.st_mode) and bool(st.st_mode & stat.S_IRUSR) and not (st.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX | 0o022)) and not st.st_mode & 0o111, 'Supplemental source mode differs')
        require(hashlib.sha256(read_regular(path,1024*1024)).hexdigest()==digest,'Supplemental source bytes differ')
        allowed_files.add(path)
    for root in roots:
        allowed_dirs.add(root)
    for path in allowed_files | roots:
        parent=path.parent
        while parent.is_relative_to(workspace) and parent != workspace:
            allowed_dirs.add(parent);parent=parent.parent
    def walk_error(error):
        raise Failure('Cannot enumerate CIPD installation: ' + str(error))
    for root in roots:
        for directory,dirs,files in os.walk(root,followlinks=False,onerror=walk_error):
            for name in files:
                require(Path(directory)/name in allowed_files, 'Undeclared CIPD installation file')
            for name in list(dirs):
                path=Path(directory)/name
                if path.is_symlink():
                    require(path in allowed_files,'Undeclared CIPD installation symlink');dirs.remove(name)
                else:
                    require(path in allowed_dirs,'Undeclared CIPD installation directory')
    return len(seen),entries
