# WebRTC CIPD payload reference

`webrtc-cipd-payloads.json` records the Linux/Android WebRTC diagnostic build's
59 installed CIPD packages, the SHA-256 of each local deployment manifest, and
the method used to check that manifest's entries against its content-addressed
CIPD instance. This file describes how the reference was prepared. The reference
is a checked-in review input; the guard cannot independently authenticate it.

The source pin is Signal WebRTC commit `d7dbd6fdddac949e89501e6ea947a344e9ffde72`.
Its DEPS SHA-256 is `3091dc4f260c6eedbfd5058dc3f53b8f9511c890d51bf7c056a2c78852245bcd`.
The audit used depot_tools commit `ce5a30784f7323ee224f3a6e7d4ae208dd6d17fe`
and a Linux x86-64 checkout with `target_os = ["android", "unix"]`. Offline
`gclient flatten` produced 161 entries. The 70 recorded Git checkouts matched
flattened pinned revisions; only the iOS Git dependency was not installed. The
59 installed CIPD package paths/names/versions matched flattened DEPS. Thirty
entries pin their instance IDs directly; the other 29 tag-to-ID mappings were
checked against the public CIPD service. Two extra snapshot packages were the
macOS and Windows GN builds, which were not installed.

CIPD's [repository protocol](https://chromium.googlesource.com/infra/luci/luci-go/+/main/cipd/api/cipd/v1/repo.proto)
defines these instance IDs as SHA-256 hashes of package ZIPs. For 54 packages,
`cipd pkg-build` reconstructed each package from its installed file set and
matched the pinned instance ID. For five packages with different archive
encoding, the original archive was fetched by its pinned ID, its SHA-256 was
verified, and every archive entry was compared to the installed file/symlink
and executable bit. Those five are Hamcrest, ICU4J, Android SDK emulator, JDK11
and sqlite4java. Eight generated version files were also checked against their
package and instance IDs. The resulting 59 manifest hashes are the values in
`webrtc-cipd-payloads.json`; its SHA-256 is
`ceb49d26ce4fc03b4dde9009c5e4863b0d40a08dd593cac61b0f4a1321dfe303`.

A reviewer can independently repeat the comparison by obtaining each pinned
package archive with `cipd pkg-fetch <package> -version <instance> -out <file>`,
verifying its content-addressed instance ID with `cipd pkg-inspect <file>`, and
comparing its entries to the corresponding deployed files and manifest. This
requires downloading packages not already cached. The reference does not bind
untracked files outside the package roots, every hook output, symlink target
contents, the depot_tools/vpython/Rust/NDK bootstrap or a built AAR. The partial
RingRTC diagnostic and release stop remain in force.
