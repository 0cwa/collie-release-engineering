# Android NDK r28 diagnostic payload reference

The native diagnostics require Android NDK `28.0.13004108` (r28). Its 8,449
archive files and symlinks are represented by canonical SHA-256 tree digest
`cc399724320a6e46fe83a068364630a9943760d7a83fd90570d660ac58786d9d`.

The reference was derived from the Linux r28 ZIP linked by the official
[Android NDK release listing](https://github.com/android/ndk/wiki/Home/24fe2d7ee3591346e0e8ae615977a15c0a4fba40),
which lists size 723,148,067 bytes and SHA-1
`894f469c5192a116d21f412de27966140a530ebc`. The downloaded ZIP matched that
SHA-1 over HTTPS; its locally computed SHA-256 was
`a186b67e8810cb949514925e4f7a2255548fb55f5e9b0824a6430d012c1b695b`. Google
provided the SHA-1 in the linked listing; this document does not claim the
SHA-256 was independently published. Every archived entry's bytes, executable
bit or symlink target matched the installed SDK NDK during the audit.

To independently reproduce the tree digest, strip the `android-ndk-r28/`
prefix from each non-directory ZIP entry. Create a JSON record with `path`,
`type` (`file` or `symlink`), and either the file's SHA-256 and `executable`
(owner-execute bit) or the symlink's `target`. Sort records by path, encode
each as UTF-8 JSON with sorted keys and separators `,` and `:`, append a
newline to each record, and SHA-256 the concatenation. The diagnostic checks
that digest and entry count before and after native builds.

SDK Manager may add `package.xml`; the diagnostic checks that it is a bounded,
regular file naming `ndk;28.0.13004108`, but does not authenticate its full
contents. Empty directories, ownership and complete permissions, concurrent
changes, host libraries, compiler behavior, other build inputs and the
reference's independent authenticity remain outside this check. Partial native
receipts and the release stop remain in force.
