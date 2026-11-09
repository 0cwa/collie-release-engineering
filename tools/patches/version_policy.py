"""Serialized local reservations; keep this durable file on the operator host.

All production sign/promote operations must use the SAME ledger and lock. This is
not a distributed lock; hosted publishing remains disabled pending operator wiring.
"""
import fcntl
from contextlib import contextmanager
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def _write_records(path, records):
    """Durably replace the ledger without overwriting a stale temp file."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.new-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(records, out, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_inventory(path, approved_sha256):
    raw = Path(path).read_bytes()
    if not re.fullmatch(r'[0-9a-f]{64}', str(approved_sha256)) or hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError('published inventory digest differs from approval')
    published = json.loads(raw)
    _validate_inventory(published)
    return published


def _validate_inventory(published):
    if not isinstance(published, list) or not published:
        raise ValueError('verified published inventory required')
    for item in published:
        if (not isinstance(item, dict) or set(item) != {'release', 'code', 'sha256'}
                or type(item['code']) is not int
                or not isinstance(item['release'], str)
                or not re.fullmatch(r'[A-Za-z0-9._+-]+', item['release'])
                or not isinstance(item['sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])):
            raise ValueError('invalid inventory')


def _ledger_records(path):
    records = json.loads(path.read_text()) if path.exists() else []
    if not isinstance(records, list):
        raise ValueError('invalid reservation ledger')
    return records


@contextmanager
def _locked(path):
    with open(str(path) + '.lock', 'a') as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def hold(path, release, code, inventory, approved_inventory_sha256):
    """Hold a version before its unsigned manifest exists.

    The pending record is deliberately unbound. reserve() atomically replaces it
    with the actual unsigned-manifest digest before the signing gate proceeds.
    """
    published = _read_inventory(inventory, approved_inventory_sha256)
    if type(code) is not int or not 1 <= code <= 2100000000:
        raise ValueError('invalid version code')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise ValueError('invalid release identity')
    path = Path(path)
    with _locked(path):
        records = _ledger_records(path)
        known = published + records
        conflicts = [item for item in known if item['release'] == release or item['code'] == code]
        if conflicts:
            if (len(conflicts) == 1 and any(conflicts[0] is row for row in records)
                    and conflicts[0]['release'] == release and conflicts[0]['code'] == code
                    and conflicts[0].get('state') == 'held' and conflicts[0].get('sha256') is None):
                return dict(conflicts[0])
            raise ValueError('release identity/code already reserved')
        if not published or code <= max(item['code'] for item in known):
            raise ValueError('version must exceed published and reserved codes')
        candidate = {'release': release, 'code': code, 'state': 'held', 'sha256': None}
        _write_records(path, records + [candidate])
        return candidate


def reserve(path, release, code, artifact_sha256, published):
    if type(code) is not int or not 1<=code<=2100000000: raise ValueError('invalid version code')
    if not re.fullmatch(r'[0-9a-f]{64}',artifact_sha256): raise ValueError('invalid artifact digest')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+',release): raise ValueError('invalid release identity')
    _validate_inventory(published)
    path=Path(path)
    with _locked(path):
        records=_ledger_records(path)
        candidate={'release':release,'code':code,'sha256':artifact_sha256}
        known=published+records
        conflicts=[item for item in known if item['release']==release or item['code']==code]
        if conflicts:
            if len(conflicts)==1:
                item=conflicts[0]
                is_ledger_record=any(item is row for row in records)
                if (is_ledger_record and item['release']==release and item['code']==code
                        and item.get('state')=='held' and item.get('sha256') is None):
                    item.pop('state')
                    item['sha256']=artifact_sha256
                    _write_records(path,records)
                    return candidate
                if item['release']==release and item['code']==code and item.get('sha256')==artifact_sha256:
                    return candidate
            raise ValueError('release identity/code already bound to different artifact or reservation')
        if code <= max(x['code'] for x in known): raise ValueError('version must exceed published and reserved codes')
        _write_records(path,records+[candidate])
        return candidate


def enforce(manifest, ledger, inventory, approved_inventory_sha256):
    """Bind signing and promotion to the same authenticated unsigned manifest."""
    if ledger is None or inventory is None or approved_inventory_sha256 is None:
        raise ValueError('production requires version ledger and authenticated published inventory')
    published = _read_inventory(inventory, approved_inventory_sha256)
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
    with _locked(path):
        records=_ledger_records(path)
        matches=[r for r in records if r['release']==manifest['version_name'] and r['code']==manifest['version_code'] and r['sha256']==manifest['unsigned_manifest_sha256']]
        if len(matches)!=1: raise ValueError('signed artifact lacks exact reservation')
        previous=matches[0].get('signed_sha256')
        if previous is not None and previous!=sha: raise ValueError('release already binds different signed bytes')
        matches[0]['signed_sha256']=sha
        _write_records(path,records)
