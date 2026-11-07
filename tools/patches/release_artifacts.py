#!/usr/bin/env python3
"""Strict local checks shared by the release build and offline signer."""
import hashlib
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

PROFILES = {'molly-minimal', 'molly-continued', 'molly-foss-continued', 'collie', 'collie-foss'}
PACKAGE = 'im.collie.app'
RC3_VERSION_CODE = 177203  # Verified on the published, signed Collie RC3 APK.


def validate_version_code(code, package):
    """Prevent releases that existing Collie RC3 installations cannot upgrade to."""
    if type(code) is not int or not 1 <= code <= 2100000000:
        raise ValueError('invalid version code')
    if package == PACKAGE and code <= RC3_VERSION_CODE:
        raise ValueError('Collie version code must exceed the verified RC3 version code')


def snapshot(source, destination):
    """Copy one regular file into a private directory without following a symlink."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('input must be a regular file: ' + str(source))
        out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(out, 'wb') as target, os.fdopen(fd, 'rb', closefd=False) as src:
            shutil.copyfileobj(src, target)
    finally:
        os.close(fd)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def apk_metadata(aapt2, apk):
    output = subprocess.run([str(aapt2), 'dump', 'badging', str(apk)], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout
    match = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']*)'", output, re.M)
    if not match:
        raise ValueError('cannot read APK package/version with aapt2')
    return match.groups()


def check_metadata(aapt2, apk, code, name):
    if apk_metadata(aapt2, apk) != (PACKAGE, str(code), name):
        raise ValueError('APK package or version differs from approved release')


def check_unsigned(apk):
    # APK v2/v3 signing blocks end immediately before the ZIP central directory.
    with zipfile.ZipFile(apk) as z:
        if any(re.fullmatch(r'META-INF/[^/]+\.(SF|RSA|DSA|EC)', n, re.I) for n in z.namelist()):
            raise ValueError('APK contains a v1 signature')
        central = z.start_dir
    with open(apk, 'rb') as f:
        if central >= 16:
            f.seek(central - 16)
            if f.read(16) == b'APK Sig Block 42':
                raise ValueError('APK contains a signing block')


def validate_manifest(m, commit, run, cert, identities=None):
    schema=m.get('schema')
    if schema not in (1,2) or m.get('commit') != commit or str(m.get('run_id')) != str(run):
        raise ValueError('manifest does not match approved commit and run')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('approved commit must be a full lowercase SHA-1')
    if schema == 2:
        expected={'source_commit','engineering_revision','workflow_revision'}
        if not isinstance(identities,dict) or set(identities)!=expected:
            raise ValueError('schema2 requires independent source/engineering/workflow approval')
        for field in expected:
            if not re.fullmatch(r'[0-9a-f]{40}', str(identities[field])) or m.get(field)!=identities[field]:
                raise ValueError('unapproved '+field)
        if m['source_commit'] != commit:
            raise ValueError('source commit differs from approved commit')
    if (schema == 1 and m.get('workflow_revision') != commit) or m.get('package') != PACKAGE:
        raise ValueError('workflow revision or package does not match approved release')
    if not re.fullmatch(r'[1-9][0-9]*', str(m.get('run_attempt', ''))):
        raise ValueError('invalid workflow run attempt')
    if not re.fullmatch(r'[0-9a-f]{64}', cert) or m.get('certificate_sha256') != cert:
        raise ValueError('manifest certificate does not match approved fingerprint')
    validate_version_code(m.get('version_code'), m['package'])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,79}', m.get('version_name', '')):
        raise ValueError('invalid version name')
    entries = m.get('apks')
    if not isinstance(entries, list) or len(entries) != 5 or {x.get('profile') for x in entries} != PROFILES:
        raise ValueError('manifest must contain exactly five profiles')
    for item in entries:
        if item.get('file') != item['profile'] + '.apk' or not re.fullmatch(r'[0-9a-f]{64}', item.get('sha256', '')):
            raise ValueError('invalid APK file or digest')
    mode = m.get('artifact_mode', 'five-profiles')
    if mode not in ('five-profiles', 'single-collie'):
        raise ValueError('unknown release artifact mode')
    if mode == 'single-collie':
        if m.get('source_profile') != 'collie':
            raise ValueError('single-collie source profile must be collie')
        if len({item['sha256'] for item in entries}) != 1:
            raise ValueError('single-collie unsigned aliases must have identical hashes')
    return entries


def validate_alias_provenance(final, unsigned, commit):
    """Bind single-build signed aliases to the authenticated unsigned manifest."""
    entries = validate_manifest(unsigned, commit, final['run_id'], final['certificate_sha256'])
    if unsigned.get('artifact_mode') != 'single-collie' or final.get('artifact_mode') != 'single-collie':
        raise ValueError('signed and unsigned artifact modes differ')
    if final.get('source_profile') != 'collie':
        raise ValueError('signed source profile must be collie')
    if final.get('package') != PACKAGE or final.get('version_code') != unsigned.get('version_code') or final.get('version_name') != unsigned.get('version_name'):
        raise ValueError('signed aliases have inconsistent package or version')
    signed = final['apks']
    if len(signed) != 5 or {item['profile'] for item in signed} != PROFILES:
        raise ValueError('final manifest must contain all five profiles')
    if len({item['signed_sha256'] for item in signed}) != 1:
        raise ValueError('signed compatibility aliases differ')
    unsigned_hash = entries[0]['sha256']
    if any(item['unsigned_sha256'] != unsigned_hash or
           item['certificate_sha256'] != final['certificate_sha256'] for item in signed):
        raise ValueError('compatibility aliases do not match unsigned provenance and certificate')
