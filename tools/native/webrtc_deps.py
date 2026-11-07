"""Verify pinned WebRTC DEPS and temporarily omit only its test-media hook."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
from pathlib import Path

class Failure(Exception):
    pass

def require(condition, message):
    if not condition:
        raise Failure(message)

# Exact block from the pinned 8037b DEPS, not a general-purpose DEPS editor.
TEST_MEDIA_HOOK = b'''  {
    # Download test resources, i.e. video and audio files from Google Storage.
    'pattern': '.',
    'action': ['download_from_google_storage',
               '--directory',
               '--recursive',
               '--num_threads=10',
               '--quiet',
               '--bucket', 'chromium-webrtc-resources',
               'src/resources'],
  },
'''

def verify_reference(data: bytes, reference: dict):
    require(hashlib.sha256(data).hexdigest() == reference.get('webrtc_deps_sha256'), 'WebRTC DEPS hash differs from the pinned source')
    fields = ('webrtc_depot_tools_revision', 'webrtc_linux_jdk_cipd_instance', 'webrtc_siso_revision', 'webrtc_gn_android_ndk_version', 'opus_model')
    for field in fields:
        value = reference.get(field)
        require(isinstance(value, str) and value and data.count(value.encode()) >= 1, 'WebRTC DEPS does not contain the recorded ' + field)
    require(data.count(TEST_MEDIA_HOOK) == 1, 'Unexpected WebRTC test-media hook')
    return {'deps_sha256': hashlib.sha256(data).hexdigest(),
            'scope': 'pinned DEPS reference only; downloaded toolchain and dependency closure unverified'}

@contextmanager
def production_hooks_only(path: Path, reference: dict):
    require(path.is_file() and not path.is_symlink(), 'WebRTC DEPS must be a regular file')
    original = path.read_bytes()
    verify_reference(original, reference)
    # The caller must operate on its disposable checkout, never a shared source tree.
    path.write_bytes(original.replace(TEST_MEDIA_HOOK, b''))
    try:
        yield
    finally:
        require(not path.is_symlink(), 'WebRTC DEPS was replaced by a symlink')
        path.write_bytes(original)
