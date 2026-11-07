# Rust diagnostic payload reference

`rust-toolchain-payloads.json` records 372 files from the Rust 1.98.1
x86-64 host compiler, Cargo, host standard library, and the standard libraries
for all four Android targets. Its SHA-256 is
`e9a644fc90306fd0eaa5d7b7323c3baea2865f460179d0b98cdbb07a2ba63cd0`.
The diagnostic checks these pinned files before and after the native build; it
requires the direct installed toolchain's `bin` directory, not rustup shims.

The reference was derived by comparing the installed files to component
archives listed in Rust's 1.98.1 channel manifest, SHA-256
`a7c8774a5fd8441c997d94c029776cbc5eb111e9d72ab5d256fa69866644347e`.
The manifest's detached signature verified against the public release key
linked by [Rust Forge](https://forge.rust-lang.org/infra/other-installation-methods.html),
fingerprint `108F66205EAEB0AAA8DD5E1C85AB96E6FA1BE5FE`. Key identity in the
audit relied on Rust's official HTTPS key link and fingerprint; the temporary
GPG keyring had no independent web-of-trust certification. Each component
archive matched the SHA-256 in the signed channel manifest. Cargo, host std,
and the four Android std components matched every compared installed payload
and component manifest. For rustc, 40 installed payload files matched; its
archive `manifest.in` was not captured for comparison, so that generated
metadata is not covered by this reference.

A reviewer can fetch the [versioned channel manifest](https://static.rust-lang.org/dist/channel-rust-1.98.1.toml),
its detached signature and Rust's release key; verify the signature and
fingerprint; then fetch the listed component archives, check their manifest
hashes and compare their contents with the reference. Temporary archives are
not required by the build-time guard.

This guard does not authenticate its checked-in reference by itself or cover
extra toolchain files, the rustc generated component manifest, host libraries,
Cargo dependencies, compiler behavior, or concurrent modification. Native build
receipts remain partial, and release remains blocked. Helpers reject an existing
output/report path before executing; a rejected rerun leaves any prior partial
receipt intact, so callers must honor the failed command rather than treating
that older receipt as a new result.
