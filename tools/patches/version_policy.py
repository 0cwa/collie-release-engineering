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
import migration_receipts as receipt_policy


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


def _read_inventory(path, approved_sha256, channel='stable'):
    raw = Path(path).read_bytes()
    if not re.fullmatch(r'[0-9a-f]{64}', str(approved_sha256)) or hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError('published inventory digest differs from approval')
    published = json.loads(raw)
    if channel=='trailblazer-beta' and isinstance(published,dict):
        # The first Beta has no prior signed index. This explicit package-bound
        # empty history still needs the independently approved raw-file digest.
        if (set(published)!={'schema','package','releases'} or
                type(published['schema']) is not int or published['schema']!=1 or
                published['package']!=receipt_policy.PROFILES[channel]['package'] or
                published['releases']!=[]):
            raise ValueError('invalid first Beta package inventory')
        return []
    if published==[]:
        raise ValueError('empty history requires explicit approved Beta package inventory')
    _validate_inventory(published,channel)
    return published


def _validate_inventory(published,channel='stable'):
    if channel not in receipt_policy.PROFILES:raise ValueError('unknown version inventory channel')
    if not isinstance(published, list) or (not published and channel=='stable'):
        raise ValueError('verified published inventory required')
    for item in published:
        fields={'release','code','sha256'} if channel=='stable' else {'release','code','sha256','package'}
        if (not isinstance(item, dict) or set(item) != fields
                or type(item['code']) is not int
                or not isinstance(item['release'], str)
                or not re.fullmatch(r'[A-Za-z0-9._+-]+', item['release'])
                or not isinstance(item['sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])
                or (channel!='stable' and item['package']!=receipt_policy.PROFILES[channel]['package'])):
            raise ValueError('invalid inventory')


def _records_for_channel(records,channel):
    if channel not in receipt_policy.PROFILES or not isinstance(records,list):
        raise ValueError('invalid version reservation channel or ledger')
    package=receipt_policy.PROFILES[channel]['package']
    known_packages={profile['package'] for profile in receipt_policy.PROFILES.values()}
    selected=[]
    for item in records:
        if not isinstance(item,dict) or not {'release','code','sha256'}<=set(item) or set(item)-{
                'release','code','sha256','state','signed_sha256','package'}:
            raise ValueError('invalid version reservation record')
        record_package=item.get('package',receipt_policy.PROFILES['stable']['package'])
        if (record_package not in known_packages or type(item['code']) is not int or
                not 1<=item['code']<=2100000000 or not isinstance(item['release'],str) or
                not re.fullmatch(r'[A-Za-z0-9._+-]+',item['release']) or
                (item['sha256'] is not None and not re.fullmatch(r'[0-9a-f]{64}',str(item['sha256']))) or
                ('state' in item and item['state']!='held') or
                ('signed_sha256' in item and not re.fullmatch(r'[0-9a-f]{64}',str(item['signed_sha256']))) or
                (item['sha256'] is None and item.get('state')!='held')):
            raise ValueError('invalid version reservation record')
        if record_package==package:selected.append(item)
    return selected


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


def hold(path, release, code, inventory, approved_inventory_sha256, *, channel='stable'):
    """Hold a version before its unsigned manifest exists.

    The pending record is deliberately unbound. reserve() atomically replaces it
    with the actual unsigned-manifest digest before the signing gate proceeds.
    """
    published = _read_inventory(inventory, approved_inventory_sha256,channel)
    if type(code) is not int or not 1 <= code <= 2100000000:
        raise ValueError('invalid version code')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', release):
        raise ValueError('invalid release identity')
    path = Path(path)
    with _locked(path):
        all_records = _ledger_records(path)
        records = _records_for_channel(all_records,channel)
        known = published + records
        conflicts = [item for item in known if item['release'] == release or item['code'] == code]
        if conflicts:
            if (len(conflicts) == 1 and any(conflicts[0] is row for row in records)
                    and conflicts[0]['release'] == release and conflicts[0]['code'] == code
                    and conflicts[0].get('state') == 'held' and conflicts[0].get('sha256') is None):
                return dict(conflicts[0])
            raise ValueError('release identity/code already reserved')
        if (not published and channel=='stable') or code <= max((item['code'] for item in known),default=0):
            raise ValueError('version must exceed published and reserved codes')
        candidate = {'release': release, 'code': code, 'state': 'held', 'sha256': None}
        if channel!='stable':candidate['package']=receipt_policy.PROFILES[channel]['package']
        _write_records(path, all_records + [candidate])
        return candidate


def reserve(path, release, code, artifact_sha256, published, *, channel='stable'):
    if type(code) is not int or not 1<=code<=2100000000: raise ValueError('invalid version code')
    if not re.fullmatch(r'[0-9a-f]{64}',artifact_sha256): raise ValueError('invalid artifact digest')
    if not re.fullmatch(r'[A-Za-z0-9._+-]+',release): raise ValueError('invalid release identity')
    _validate_inventory(published,channel)
    path=Path(path)
    with _locked(path):
        all_records=_ledger_records(path)
        records=_records_for_channel(all_records,channel)
        candidate={'release':release,'code':code,'sha256':artifact_sha256}
        if channel!='stable':candidate['package']=receipt_policy.PROFILES[channel]['package']
        known=published+records
        conflicts=[item for item in known if item['release']==release or item['code']==code]
        if conflicts:
            if len(conflicts)==1:
                item=conflicts[0]
                is_ledger_record=any(item is row for row in records)
                if (is_ledger_record and item['release']==release and item['code']==code
                        and item.get('state')=='held' and item.get('sha256') is None):
                    if channel=='trailblazer-beta' and code<=max((x['code'] for x in published),default=0):
                        raise ValueError('version must exceed current published codes before binding a hold')
                    item.pop('state')
                    item['sha256']=artifact_sha256
                    _write_records(path,all_records)
                    return candidate
                if item['release']==release and item['code']==code and item.get('sha256')==artifact_sha256:
                    return candidate
            raise ValueError('release identity/code already bound to different artifact or reservation')
        if code <= max((x['code'] for x in known),default=0): raise ValueError('version must exceed published and reserved codes')
        _write_records(path,all_records+[candidate])
        return candidate


def enforce(manifest, ledger, inventory, approved_inventory_sha256, *, channel='stable'):
    """Bind signing and promotion to the same authenticated unsigned manifest."""
    if ledger is None or inventory is None or approved_inventory_sha256 is None:
        raise ValueError('production requires version ledger and authenticated published inventory')
    if channel not in receipt_policy.PROFILES:raise ValueError('unknown release version channel')
    published = _read_inventory(inventory, approved_inventory_sha256,channel)
    code=manifest['version_code']
    # The new route never signs a new artifact at or below the verified RC1.
    # Legacy parsing/redistribution remains available through the legacy adapter.
    if channel=='stable' and code <= 177702:
        raise ValueError('new production signing/promotion must exceed published RC1 code 177702')
    identity=manifest.get('unsigned_manifest_sha256') or manifest.get('_unsigned_manifest_sha256')
    if identity is None: raise ValueError('missing unsigned manifest identity')
    return reserve(ledger,manifest['version_name'],code,identity,published,channel=channel)


def bind_signed(ledger, manifest, *, channel='stable'):
    """Refuse a different signed artifact for an already reserved identity."""
    path=Path(ledger)
    if channel not in receipt_policy.PROFILES:raise ValueError('unknown release version channel')
    sha=manifest['apks'][0]['signed_sha256']
    if not re.fullmatch(r'[0-9a-f]{64}',sha) or any(x['signed_sha256']!=sha for x in manifest['apks']):
        raise ValueError('single-collie signed aliases differ')
    with _locked(path):
        all_records=_ledger_records(path)
        records=_records_for_channel(all_records,channel)
        matches=[r for r in records if r['release']==manifest['version_name'] and r['code']==manifest['version_code'] and r['sha256']==manifest['unsigned_manifest_sha256']]
        if len(matches)!=1: raise ValueError('signed artifact lacks exact reservation')
        previous=matches[0].get('signed_sha256')
        if previous is not None and previous!=sha: raise ValueError('release already binds different signed bytes')
        matches[0]['signed_sha256']=sha
        _write_records(path,all_records)
