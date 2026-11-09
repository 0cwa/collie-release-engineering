# Native libraries and proxy safeguards

For **8.35.1-2-rc1**, Collie packages Signal libraries with Molly-derived protections and Collie adaptations—not stock Signal binaries or unchanged Molly releases. The [native lock](native-lock.json) pins source revisions, attributed patches and accepted binary hashes. The application uses the `im.collie.native` coordinates below; WebRTC is part of RingRTC.

| Library | Signal source | Protections and adaptations |
| --- | --- | --- |
| `libsignal-client` / `libsignal-android` `0.106.1-collie.1` | [`904c8dc`](https://github.com/signalapp/libsignal/commit/904c8dc8216073b078a5f2f5090e3da8382e6a92) | [Oscar Mira's Molly proxy-only change and separate 0cwa tests](patches/libsignal-0.106.1-attributed.mbox). |
| `ringrtc-android` `2.74.0-collie.1` | [`bdbb0dd`](https://github.com/signalapp/ringrtc/commit/bdbb0dd1b9f418850dfe1fa3cd4b1c2b1123436f) | [0cwa's port and credential-order fix](patches/ringrtc-2.74-attributed.mbox), derived from [Molly 2.73.0-1](https://github.com/mollyim/ringrtc/commit/78dfbe7d4ed8d46f372b67701543e3678c64e5f0). |
| WebRTC, 8037b line | [`d7dbd6f`](https://github.com/signalapp/webrtc/commit/d7dbd6fdddac949e89501e6ea947a344e9ffde72) | [Molly-derived proxy adaptation](patches/webrtc-8037b-molly-proxy-UNTESTED.patch), plus Collie's [unknown-proxy rejection](patches/webrtc-unknown-proxy-fix.patch) and [SOCKS IPv6 fix](patches/webrtc-socks-ipv6-fix.patch). |

WebRTC's [Molly reference changes](https://github.com/mollyim/webrtc/compare/25e48ee75ff1b6fcab10c6a200f233d449dd421e...50bb01c1c09ec8f141b04fd9b869b012dd2d8345) are credited to Oscar Mira, including reverts but not authorship of the reverted work. The 8037b adaptation is derived work, not those original commits; Collie's additional fixes are not attributed to Molly.

## Proxy behavior and testing

User-selected SOCKS proxies are designed to stop connections rather than silently bypass a failed proxy, with proxy-aware DNS. Optional Android system proxies can fall back to direct connections if configuration fails. When supplied proxy settings, RingRTC/WebRTC uses SOCKS5 or HTTPS CONNECT and disables UDP/STUN and local candidate paths that could bypass the proxy.

Four libsignal proxy-mode tests passed in the [recorded local diagnostic](README.md#local-diagnostic-candidate-provenance). Application tests cover [socket refusal](https://github.com/0cwa/collieim-android/blob/65c1ddb8be99cf50e63a33832920ab0ec42a46c6/app/src/test/java/org/thoughtcrime/securesms/net/MollyProxyFailClosedTest.kt) and [user/system proxy policy](https://github.com/0cwa/collieim-android/blob/65c1ddb8be99cf50e63a33832920ab0ec42a46c6/core/network/src/test/java/org/signal/network/config/LibSignalProxyPolicyTest.kt); the exact-source native JVM suite is not claimed passed. The [unknown-type](patches/test_webrtc_unknown_proxy.py) and [IPv6](patches/test_webrtc_socks_ipv6.py) harnesses exercise extracted C++ with stand-ins, not live sockets or full WebRTC.

The [PR #45](https://github.com/0cwa/collieim-android/pull/45) local unsigned APK build and [native publication readbacks](https://github.com/0cwa/collieim-android/blob/65c1ddb8be99cf50e63a33832920ab0ec42a46c6/build-inputs/native-publication.json) are build/input evidence, **not proof of end-to-end proxy safety or independently reproducible native builds**. Runtime and MollySocket acceptance remain separate and unperformed in that record.
