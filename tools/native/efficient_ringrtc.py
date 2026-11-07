#!/usr/bin/env python3
"""Run the pinned RingRTC builder without duplicating WebRTC intermediate trees."""
from pathlib import Path
import sys

def adapt(text):
    changes = [
        ("        './gradlew',\n        '-PringrtcVersion={}", "        os.environ['COLLIE_VERIFIED_GRADLE'],\n        '--project-dir', gradle_dir,\n        '-PringrtcVersion={}"),
        ("ninja_args = ['third_party/siso/cipd/siso', 'ninja', '-C', webrtc_output_dir] + NINJA_TARGETS", "ninja_args = ['third_party/siso/cipd/siso', 'ninja', '-local_jobs', '12', '-C', webrtc_output_dir] + NINJA_TARGETS"),
        ('shutil.copytree(webrtc_output_dir, output_dir, dirs_exist_ok=True, ignore_dangling_symlinks=True)', 'os.makedirs(os.path.join(output_dir, "lib.unstripped"), exist_ok=True)\n        for lib in WEBRTC_SO_LIBS:\n            shutil.copyfile(os.path.join(webrtc_output_dir, "lib.unstripped", lib), os.path.join(output_dir, "lib.unstripped", lib))'),
        ('d = os.path.basename(jar)\n            os.makedirs(d, exist_ok=True)', 'os.makedirs(os.path.dirname(os.path.join(output_dir, jar)), exist_ok=True)'),
        ('[GetArchBuildDir(build_dir, arch, debug) for arch in ARCHS]', "[GetArchBuildDir(os.path.join(webrtc_src_dir, 'out'), arch, debug) for arch in ARCHS]"),
    ]
    for old, new in changes:
        if text.count(old) != 1:
            raise RuntimeError('Pinned RingRTC builder differs from the expected source; refusing to adapt it')
        text = text.replace(old, new, 1)
    return text

def main():
    source = Path(sys.argv[1]).resolve(strict=True)
    text = adapt(source.read_text())
    sys.argv = [str(source), *sys.argv[2:]]
    exec(compile(text, str(source), 'exec'), {'__name__': '__main__', '__file__': str(source)})

if __name__ == '__main__':
    main()
