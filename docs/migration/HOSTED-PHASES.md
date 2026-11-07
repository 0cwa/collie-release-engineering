# Hosted source proof and build eligibility

The patch workflow always reconstructs every selected historical profile with
`release.py`, verifying the complete source checkpoints and upstream identities.
Those source proofs are separate from Android/native/instrumentation acceptance.

The current declared multi-artifact native integration needs the three local
native source repositories or separately approved pinned native inputs. The
hosted workflow provides neither accepted route, so it records native, Android
and instrumentation execution as **NOT RUN** and leaves them disabled. Its source
proof artifacts include `hosted-eligibility.json`; a green source proof is not
release/cutover approval. Native public availability and actual direct builds
remain blocked on operator provisioning and acceptance.

`hosted_eligibility.py` reuses the existing native-lock selector and its shape,
path and current-port digest checks. This is configuration-based, independent
of stack labels such as `main-maintenance`. Malformed/missing required inputs
fail instead of becoming eligible. Historical configurations without native
backports, or using the older supported single-backport route, retain the
existing hosted native validation/build flow. Eligibility itself always reports
NOT RUN; actual build results remain separate evidence.

The local release/native route, required native integrity checks and Signal8.35
unsigned-release block are unchanged. No hosted signing or production build is
enabled by this classification. Once public inputs and real acceptance exist,
a separately reviewed workflow change can enable the canonical source build.
