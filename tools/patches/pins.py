#!/usr/bin/env python3
"""Every upstream pin compiled into the patch tools and workflows.

The tools refuse inputs that differ from the pinned releases, so moving to a new Signal,
Molly or fork release means changing each pin below in every file it names, then the data
pins in `molly-build.json` and the series provenance. `python3 tools/patches/pins.py`
prints the table. `test_pins.py` fails when a tool or workflow contains an upstream commit,
tree, port directory, fork release or source URL missing from this list, or when a listed
pin is gone from a file it names. Files absent from a branch are skipped.
"""
import sys

SCOPE = 'tools/patches/scope.py'
REPORT = 'tools/patches/port_report.py'
ORIGIN = 'tools/patches/line_origin.py'
PREPARE = 'tools/patches/prepare_port_report.py'
PREFLIGHT = 'tools/patches/preflight.sh'
RELEASE_WORKFLOW = '.github/workflows/molly-patch-release.yml'
SYNC_WORKFLOW = '.github/workflows/sync.yml'
FORK = 'tools/patches/fork_profiles.py'
PROFILES = 'tools/patches/release_profiles.py'
FETCH_REVIEWED = 'tools/patches/fetch_reviewed_profiles.py'
BUILD = 'tools/patches/build.py'
UNSIGNED_WORKFLOW = '.github/workflows/molly-unsigned-release.yml'

# (value, meaning, upstream whose new release changes it, files that contain it)
PINS = [
    ('b06a83f6e4584f27811c3f406579b7db296f4b59', 'audited clean integration entries manifest', 'none', [SCOPE]),
    ('7f0eb01a0ab472deeef3b528e7f508425044fa6a', 'audited historical release base for clean main', 'none', [SCOPE, RELEASE_WORKFLOW]),
    ('7d486880ddc991af0d36f1e4709030255ef4dbad', 'reviewed fork base for Signal 8.35.1', 'none', [SCOPE]),
    ('1861543f3ba23b0bc396cb39a0595346537b6df6', 'audited main transition boundary', 'none', [SCOPE]),
    ('89bd7d6368a509470c4612d079746f66dfac8b55', 'reviewed release profile composition', 'none', [SCOPE]),
    ('0e202dda90785771ff8c87526eaaf30c655f54f3', 'Molly 8.19.2-4 release commit, the Molly input', 'Molly',
     [SCOPE, REPORT, RELEASE_WORKFLOW, '.github/workflows/molly-unsigned-release.yml']),
    ('c47a74ba0961903eeaeda6d22347a990056c3e83', 'Signal 8.19.2 commit under Molly 8.19.2-4', 'Molly',
     [SCOPE, REPORT]),
    ('3cd0bb01cf840e4b702187085432020d163d80ce', 'Molly 8.19.2-4 tree that stack 2 must rebuild', 'Molly', [SCOPE]),
    ('7a280ac23b61cef2c09009a6dbed02773fff3dfa', 'tree of the protected patches/molly forest', 'Molly', [SCOPE]),
    ('6e9dddc01f1b26e608ac683807c51e139c141ed2', 'Signal 8.33.2 input and historical fork base', 'Signal and fork', [SCOPE, FORK, ORIGIN]),
    ('b13bff463547475bbe1ae55335add689f52532e0', 'tw-hx fork source head', 'fork', [FORK, SCOPE]),
    ('fc0daac37774d9c20238a34ac4786d9398adc07f', 'historical fork retention ancestor', 'fork', [FORK]),
    ('signal-8.33.2', 'historical port directory', 'Signal',
     [SCOPE, RELEASE_WORKFLOW, FETCH_REVIEWED]),
    ('signal-8.35.1', 'current port directory', 'Signal', [PROFILES, SCOPE, ORIGIN]),
    ('a74583b01e260944f699c4da03ba4e8ce04de32b', 'Signal 8.35.1 source commit', 'Signal', [FORK, SCOPE, BUILD, RELEASE_WORKFLOW, UNSIGNED_WORKFLOW]),
    ('8.33.2.0-FOSS', 'tw-hx fork release named by the upstream bundle ref', 'fork', [FORK, SCOPE]),
    ('github.com/tw-hx/Signal-Android', 'fork source URL', 'fork', [FORK, PROFILES, SCOPE]),
    ('github.com/mollyim/mollyim-android.git', 'Molly source URL', 'none', [PREFLIGHT, PREPARE, RELEASE_WORKFLOW, '.github/workflows/molly-unsigned-release.yml']),
    ('github.com/signalapp/Signal-Android.git', 'Signal source URL', 'none', [PREPARE, SYNC_WORKFLOW, FORK]),
]

# Layer IDs that tools name directly. A new layer in these positions means updating them.
LAYER_NAMES = [
    ('followups/network-initialization-cycle', 'historical Molly Minimal boundary', [FORK]),
    ('followups/persian-molly-donation', 'historical universal donation-label fix', [FORK]),
    ('followups/unifiedpush-registration-retry', 'historical Continued boundary', [FORK]),
    ('followups/view-once-open-guard', 'shared Collie profile boundary', [FORK, PROFILES]),
    ('firebase-free-build', 'first fork layer', [FORK, PROFILES]),
    ('retention-controls', 'historical fork layer name', [PROFILES]),
    ('retention-controls-fixes', 'historical fork fix layer name', [PROFILES]),
    ('collie-icon', 'Collie launcher icon layer', [FORK, PROFILES]),
    ('collie-branding', 'Collie theme and app identity layer', [FORK, PROFILES]),
    ('collie-visuals', 'Collie splash and automatic chat colors', [FORK, PROFILES]),
]


def main():
    print('| Pin | Meaning | Changes with | Files |\n|---|---|---|---|')
    for value, meaning, upstream, files in PINS:
        print(f'| `{value}` | {meaning} | {upstream} | ' + ', '.join(f'`{f}`' for f in files) + ' |')
    print('\n| Layer named by tools | Role | Files |\n|---|---|---|')
    for value, meaning, files in LAYER_NAMES:
        print(f'| `{value}` | {meaning} | ' + ', '.join(f'`{f}`' for f in files) + ' |')
    return 0


if __name__ == '__main__':
    sys.exit(main())
