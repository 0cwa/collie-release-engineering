"""Verify an unsigned port APK against explicitly staged native candidates.

This checks selected bytes, not compiler trust or independent reproducibility.
"""
import hashlib
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import release_artifacts

ABIS = ('arm64-v8a', 'armeabi-v7a', 'x86_64')


def verify(apk, lock_path, stage, sdk, build_tools, ndk, version_name, version_code, firebase_free):
    apk, stage, sdk = map(Path, (apk, stage, sdk))
    lock = json.loads(Path(lock_path).read_text())
    aapt = sdk / 'build-tools' / build_tools / 'aapt2'
    strip = sdk / 'ndk' / ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip'
    release_artifacts.check_metadata(aapt, apk, version_code, version_name)
    release_artifacts.check_unsigned(apk)
    badging = subprocess.check_output([str(aapt), 'dump', 'badging', str(apk)], text=True)
    manifest = subprocess.check_output([str(aapt), 'dump', 'xmltree', '--file', 'AndroidManifest.xml', str(apk)], text=True)
    fcm = 'org.thoughtcrime.securesms.gcm.FcmReceiveService' in manifest
    if fcm == firebase_free:
        raise ValueError('APK Firebase service differs from the selected profile')
    if re.search(r'^application-debuggable(?:\s|$)', badging, re.M):
        raise ValueError('release APK is debuggable')
    expected = {}
    with tempfile.TemporaryDirectory(prefix='collie-native-check-', dir=stage.parent) as tmp:
        with zipfile.ZipFile(apk) as package:
            names = package.namelist()
            if len(names) != len(set(names)):
                raise ValueError('release APK has duplicate zip entries')
            if any(x.startswith('lib/') and x.endswith('/libsignal_jni_testing.so') for x in names):
                raise ValueError('testing JNI is present in release APK')
            known_names = {lib for module in lock['modules'] for lib in module['native_libraries']}
            allowed = {'lib/' + abi + '/' + lib for abi in ABIS for lib in known_names}
            for entry in names:
                if entry.startswith('lib/') and entry.rsplit('/', 1)[-1] in known_names and entry not in allowed:
                    raise ValueError('unexpected native library ABI: ' + entry)
            for module in lock['modules']:
                if not module['native_libraries']:
                    continue
                name, version = module['name'], module['output_version']
                archive = stage / 'maven' / 'im/collie/native' / name / version / (name + '-' + version + '.aar')
                with zipfile.ZipFile(archive) as native:
                    for abi in ABIS:
                        for lib in module['native_libraries']:
                            path = Path(tmp) / lib
                            path.write_bytes(native.read('jni/' + abi + '/' + lib))
                            subprocess.run([str(strip), '--strip-unneeded', str(path)], check=True)
                            packaged = package.read('lib/' + abi + '/' + lib)
                            digest = hashlib.sha256(path.read_bytes()).hexdigest()
                            if hashlib.sha256(packaged).hexdigest() != digest:
                                raise ValueError('native library mismatch: ' + abi + '/' + lib)
                            expected[abi + '/' + lib] = digest
    return {'apk_sha256': release_artifacts.sha256(apk), 'native_libraries': expected,
            'native_lock_sha256': release_artifacts.sha256(lock_path),
            'trust': 'trusted local native builder; independent reproducibility not established'}
