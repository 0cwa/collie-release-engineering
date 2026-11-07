"""Serialized local reservations; keep this durable file on the operator host.

All production sign/promote operations must use the SAME ledger and lock. This is
not a distributed lock; hosted publishing remains disabled pending operator wiring.
"""
import fcntl
import json
import os
import re
from pathlib import Path


def reserve(path, release, code, artifact_sha256, published):
    if type(code) is not int or not 1<=code<=2100000000: raise ValueError('invalid version code')
    if not re.fullmatch(r'[0-9a-f]{64}',artifact_sha256): raise ValueError('invalid artifact digest')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+',release): raise ValueError('invalid release identity')
    if not isinstance(published,list) or not published: raise ValueError('verified published inventory required')
    for item in published:
        if set(item) != {'release','code','sha256'} or type(item['code']) is not int: raise ValueError('invalid inventory')
    path=Path(path)
    with open(str(path)+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        records=json.loads(path.read_text()) if path.exists() else []
        candidate={'release':release,'code':code,'sha256':artifact_sha256}
        known=published+records
        for item in known:
            if item['release']==release or item['code']==code:
                if any(item[k] != v for k,v in candidate.items()): raise ValueError('release identity/code already bound to different artifact')
                return candidate  # Re-distribution of identical signed bytes needs no bump.
        if code <= max(x['code'] for x in known): raise ValueError('version must exceed published and reserved codes')
        temporary=path.with_name(path.name+'.new')
        with temporary.open('w') as out:
            json.dump(records+[candidate],out,sort_keys=True); out.flush(); os.fsync(out.fileno())
        os.replace(temporary,path)
        fd=os.open(path.parent,os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        return candidate


def enforce(manifest, ledger, inventory, approved_inventory_sha256):
    """Bind signing and promotion to the same authenticated unsigned manifest."""
    import hashlib
    if ledger is None or inventory is None or approved_inventory_sha256 is None:
        raise ValueError('production requires version ledger and authenticated published inventory')
    raw=Path(inventory).read_bytes()
    if hashlib.sha256(raw).hexdigest() != approved_inventory_sha256:
        raise ValueError('published inventory digest differs from approval')
    published=json.loads(raw)
    code=manifest['version_code']
    # The new route never signs a new artifact at or below the verified RC1.
    # Legacy parsing/redistribution remains available through the legacy adapter.
    if code <= 177702:
        raise ValueError('new production signing/promotion must exceed published RC1 code 177702')
    identity=manifest.get('unsigned_manifest_sha256') or manifest.get('_unsigned_manifest_sha256')
    if identity is None: raise ValueError('missing unsigned manifest identity')
    return reserve(ledger,manifest['version_name'],code,identity,published)


def bind_signed(ledger, manifest):
    """Refuse a different signed artifact for an already reserved identity."""
    path=Path(ledger)
    sha=manifest['apks'][0]['signed_sha256']
    if not re.fullmatch(r'[0-9a-f]{64}',sha) or any(x['signed_sha256']!=sha for x in manifest['apks']):
        raise ValueError('single-collie signed aliases differ')
    with open(str(path)+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        records=json.loads(path.read_text())
        matches=[r for r in records if r['release']==manifest['version_name'] and r['code']==manifest['version_code'] and r['sha256']==manifest['unsigned_manifest_sha256']]
        if len(matches)!=1: raise ValueError('signed artifact lacks exact reservation')
        previous=matches[0].get('signed_sha256')
        if previous is not None and previous!=sha: raise ValueError('release already binds different signed bytes')
        matches[0]['signed_sha256']=sha
        temporary=path.with_name(path.name+'.new')
        with temporary.open('w') as out:
            json.dump(records,out,sort_keys=True);out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
