# MollySocket TLS feature layer

The next-release source must include the reviewed TLS trust-context fix from
application commit `6f2595515d8466da725c1dda65f252ef02d05456`. Its parent
`8b119e357d1c9c6863eff31756660bc07d6cfb8b` has the complete source tree recorded
as `b5fbed355186be1e728d556a48718e7158e7ab8f` in the current candidate plan.

`source/layers/mollysocket.patch` carries the exact two-file delta from that
commit: `BlacklistingTrustManager` retains `Socket` and `SSLEngine` context when
delegating to Android's extended trust manager, checks the existing certificate
blacklist first, and falls back to the legacy delegate when contextual trust is
unavailable. The same patch includes the matching Kotlin regression cases.
`source/layers/mollysocket.json` pins the original commit, author, parent/result
trees, changed paths and patch digest. The generated engineering feature commit
must identify itself as composite and cite this original provenance.

This layer includes only commit `6f259551`; it does not include later PR46
README restoration or permanent patch-only policy documentation. Apply the
patch after the prior complete source tree and verify the complete resulting
tree against the independently reviewed plan before making G/I candidates.

The engineering suite checks the pinned patch and retained test cases. It does
not run Android/JVM tests, compile the application, or establish production
certificate behavior. Run the included Kotlin tests on the final regenerated
source candidate; prior PR46 reports of JVM and Android 15 debug probes are not
independent evidence for a new candidate.
