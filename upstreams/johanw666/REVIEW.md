# Johan fork review: 2026-11-14

## Scope and provenance

Reviewed [`v8.36.0.0-JW`](https://github.com/johanw666/Signal-Android/tree/v8.36.0.0-JW),
commit [`8f4ad8577344e844945ee8e73d95fd1fcc7208bc`](https://github.com/johanw666/Signal-Android/commit/8f4ad8577344e844945ee8e73d95fd1fcc7208bc),
against its clean Signal parent
[`b8544a813786e2442f145735e500e78df6a33014`](https://github.com/signalapp/Signal-Android/commit/b8544a813786e2442f145735e500e78df6a33014)
(`v8.36.0`). The custom branch is `8.36.0.0-JW`; the tag has an additional `v`
prefix. The fork's `main` tracks clean Signal, so inspecting only `main` misses
the custom changes.

Compatibility was checked against Collie app main
`75aac538a75b2c8ed907050a5b3f232a48eb6b36` (Signal 8.35.2). Taking a selected
feature from this snapshot does not upgrade Collie's Signal baseline to 8.36.0.

The custom changes in this snapshot are grouped in one commit authored by
`Johan <johanw@xs4all.nl>`. The ledger attributes observed code to that reviewed
commit; it does not claim that commit first introduced every feature. The source
has an AGPLv3 license, and imported files must retain their existing notices and
any earlier contributor credits.

The review selects feature ideas and code suitable for Collie. It does not treat
the entire custom fork as a maintained security boundary or a replacement for
Signal/Molly updates.

## Selection and implementation order

| Priority | Feature | Decision and next step |
| --- | --- | --- |
| 1 | Blocked inviter and automatic profile sharing | Propose one narrow app PR with a state-processor regression. |
| 2 | Text-only webpage previews | Implemented as an independent app proposal with an early image gate, cache/submission guards and a Chats setting; app checks and API 35 verification remain pending. |
| 3 | Group admission policy | Redesign the policy and member-state handling before exposing a setting. |
| 4 | Attachment-only deletion | Define sync, quote, backup and thread-cache behavior before implementing. |
| Existing | Initial passphrase keyboard focus | Already supplied by Molly in Collie; avoid a duplicate port. |

The [machine-readable ledger](review.json) has exact paths, decisions and intended
verification. `proposed` means reviewed for an app PR, not merged or released.

### 1. Blocked inviter profile sharing

In `GroupsV2StateProcessor.kt`, automatic profile sharing can be enabled because
the inviter is a system contact or a contact already trusted for profile sharing.
Johan's custom code adds an explicit `!addedBy.isBlocked` condition to that
trusted-inviter branch.

Collie has an earlier path that leaves a group for a blocked inviter, but that
path excludes a user who previously requested to join. When that request is
approved, the earlier leave check does not supply the same blocked-inviter guard.
The automatic-sharing decision should therefore check blocking itself. This is
a narrow trust-policy correction with no new permission, setting or network
dependency. Preserve self-add handling and ordinary unblocked contacts.

Only this guard is selected from the much larger group-policy diff. Add a
regression around a blocked profile-sharing contact approving an earlier join
request, plus an unblocked control. Do not claim broader admission-policy
coverage from that test.

### 2. Text-only previews

A control that keeps webpage title/description while avoiding the optional image
would reduce image downloads and image-decoder work. Johan's implementation
demonstrates a separate user preference, but its image check happens after the
image has already been obtained and decoded. When images are disabled, even
successful compression does not enter the branch containing `break`, so the
loop may try every compression size.

The implemented Collie proposal uses this feature idea without copying the
fork's gating and compression behavior. It gates the optional webpage image
request before fetching or decoding, retains the existing title/description path
and keeps the current proxy policy. Signal sticker, group and call-link cards
retain their existing behavior.

#### Implemented proposal

1. **Web link preview images** appears immediately below the existing
   **Generate link previews** control in Chats. It uses the current Compose
   settings screen, state and events, plus the app's settings repository and
   ViewModel. The new row is disabled while its parent setting is off, and the
   saved image choice is retained when the parent is toggled.
2. `SettingsValues.isWebLinkPreviewImagesEnabled` is a local boolean that defaults
   to `true`, preserving existing behavior for upgrades and old backups. Its key
   is included in the existing backup list. It stays independent of
   `isLinkPreviewsEnabled`. The existing master switch sends a
   `MultiDeviceConfigurationUpdateJob`; there is no corresponding protocol field
   for image preference, so the new preference does not schedule remote sync.
3. The generic-web branch of `LinkPreviewRepository.getLinkPreview` checks the
   preference before `fetchThumbnail`, retaining useful metadata and the existing
   proxy/DNS/redirect policy. The settings description explains that pages are
   still fetched for titles and descriptions. Signal sticker, group and call-link
   cards keep their explicit existing routes.
   Direct image URLs can deliver response bytes on the initial request before
   their content type is known; the feature avoids the separate thumbnail request
   and image decoding when disabled, without claiming that no image bytes can
   ever arrive.
4. A shared generic-web image policy also handles restored and resolved previews
   in both `LinkPreviewViewModelV2` and the legacy `LinkPreviewViewModel`. Settings
   refresh removes stale images; Rx disposal and the legacy ViewModel's request
   generations invalidate callbacks from canceled requests. The immutable `LinkPreview.withoutThumbnail()` copy clears
   both `thumbnail` and `attachmentId` while preserving raw text fields, avoiding
   a second round of HTML decoding.
5. The latest preference is applied at conversation, text-story and share-sheet
   submission boundaries, including explicitly supplied conversation previews
   and cached `MultiShareArgs` inputs. Empty web cards are omitted. Image-only
   share drafts retain their URL; text stories receive a URL-only fallback card
   so a link-only story does not become blank. Composer resume paths refresh
   visible previews. The proposal does not introduce a global forwarding filter,
   remove received images or rewrite stored message history or submitted jobs.
6. Tests were added for optional-image request counts, response closing,
   metadata/default-on behavior, special cards, restored caches, preference
   changes and stale callbacks in both preview ViewModels. Actual share and
   text-story `send()` tests capture outgoing arguments after a preference
   change, including attachment-ID removal and image-only URL retention. Settings
   persistence/backup inclusion, settings ViewModel behavior and the Compose row
   also have regression tests.

[App PR #143](https://github.com/0cwa/collieim-android/pull/143) is an
independently reviewable `Layer: web-link-preview-images` proposal,
not an adopted change. The app tests have not been compiled or executed in this
environment. CI and the required API 35 screen verification remain outstanding
before merge; neither the source review nor these test additions establish a
passing Android build.

### 3. Group admission controls

The broader policy could let users decide who may add them to groups. The
reviewed implementation needs redesign: it dereferences `addedBy!!` before a
later null check; a computed `mayAdd` value in the full-member path is unused;
and a linked-device pending-member path reverses the decision. These are
concrete reasons to separate it from the selected blocked-inviter guard.

Define a common decision model for pending, requesting and full members, primary
and linked devices, self-initiated joins and unknown inviters. Establish those
transitions with tests before adding the settings UI.

### 4. Attachment-only deletion

Keeping text while removing large media is useful, but `AttachmentUtil.kt`,
`MessageTable.kt` and `MediaTable.kt` changes reach beyond a menu option. A port
needs a clear contract for local-only removal versus linked-device sync, quoted
attachments, encrypted backup/restore, attachment counts and cached thread or
gallery previews. Treat this as a separate storage feature with its own tests.

Johan suppresses message-deletion sync when the last attachment is removed, but
multi-attachment deletion still enqueues attachment sync. A consistent local-only
feature must decide both paths explicitly. Relevant Collie callers include
`MediaViewerRepository.localDelete`, `MediaPreviewRepository.localDelete` and
`MediaDeleteProgressViewModel.start`, which consume the records returned by
`AttachmentUtil`. Keep remote-delete actions separate and exercise the existing
`AttachmentUtilTest` and `MediaTableTest_galleryMedia` surfaces alongside new
caption, quote and synchronization regressions.

## Excluded implementation families

| Family | Reason to leave it out |
| --- | --- |
| Raw database/XML/WhatsApp import and export | Raw export writes database and attachment secrets to external plaintext files before archiving. An empty password makes an unencrypted ZIP, archive failure is ignored before deleting the staging files, and the feature family adds all-files access. A safe migration design would be independent work. |
| View-once and remote/admin deletion bypasses | These alter message-retention semantics and are unrelated to the selected reliability/privacy correction. |
| Internal-user, expiry and client-limit overrides | These change upstream service and release assumptions; they are not dependencies of the selected features. |
| Google Maps controls and fork build identity | Collie already has its own map, branding and release choices. Keep fork-specific Google dependencies, keys and build overrides out of app ports. |

The original WhatsApp tooling also credits earlier contributors in the fork's
README, and some helpers have their own source attribution. An AGPL-compatible
project license does not erase those notices if any later design reuses code.

## PR boundaries

The engineering PR introduces the source index, this review and offline ledger
checks. [App PR #142](https://github.com/0cwa/collieim-android/pull/142) carries
only `Layer: group-profile-sharing`, upstream
attribution and focused regression coverage. Future feature PRs should update
their individual ledger entries; [app PR #143](https://github.com/0cwa/collieim-android/pull/143)
uses `Layer: web-link-preview-images`. App features need not depend on or re-import the
historical patch forest. After a PR merges, record its actual app SHA and move
that entry from `proposed` to `adopted` in a small engineering follow-up.
