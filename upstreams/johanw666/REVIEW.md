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
| 2 | Text-only webpage previews | Recommend for a later PR after choosing the setting location; move the gate before the image request. |
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

A Collie implementation should stop the optional webpage image request before
fetching or decoding, retain the existing title/description path and keep the
current proxy policy. Special Signal previews need explicit tests rather than a
blanket change to all preview types. Keep today's behavior as the default, agree
the setting location under the app's UI workflow, and test request counts with
images disabled. The current fork code should not be copied wholesale.

#### Concrete port design

1. Propose **Web link preview images** immediately below the existing **Generate
   link previews** control in Chats. The app's `AGENTS.md` requires the location
   of new UI to be chosen explicitly; this is a proposal, not a new control in
   the first patchset. Extend the current Compose `ChatsSettingsScreen`,
   `ChatsSettingsState` and `ChatsSettingsEvents`, plus the app's
   `ChatsSettingsViewModel` and `ChatsSettingsRepository`. Do not bring back
   Johan's legacy settings entry points.
2. Store a local boolean in `SettingsValues`, defaulting to `true` to preserve
   existing behavior, and include it in the existing backup-key list. Keep it
   independent of `isLinkPreviewsEnabled`. The existing master switch sends a
   `MultiDeviceConfigurationUpdateJob`; there is no corresponding protocol field
   for image preference, so the new preference should not claim to sync.
3. In the generic-web branch of `LinkPreviewRepository.getLinkPreview`, gate
   before `fetchThumbnail`. Retain useful metadata, existing proxy/DNS/redirect
   policy, cancellation and error behavior. Explain that webpage metadata still
   requires contacting the site. Leave Signal sticker, group and call-link cards
   on their explicit existing routes; the option's label must match that scope.
   Direct image URLs can deliver response bytes on the initial request before
   their content type is known, so promise avoidance of the separate thumbnail
   request and decoding, not that no image bytes can ever arrive.
4. Apply a shared generic-web image policy when a preview is restored, adopted
   or sent. `LinkPreviewViewModelV2` retains `savedLinkPreviewState`, adopts
   already-resolved state in `onLinkPreviewResolved`, and skips refetching when
   `activeUrl` is unchanged. Its `onSend()` returns the stored preview. The
   legacy `LinkPreviewViewModel` also caches previews. Merely changing the fetch
   path can therefore send an image obtained before the setting changed.
   Add an immutable thumbnail-free copy operation to `LinkPreview` that clears
   both `thumbnail` and `attachmentId` while preserving its raw text fields;
   rebuilding from HTML-decoded getters can alter the title and description.
5. Cover send paths that bypass those view-model send helpers:
   `mediasend/v2/text/TextStoryPostCreationFragment.getLinkPreview()` hands its
   snapshot to the story sender, and
   `sharing/interstitial/ShareInterstitialViewModel.send()` places a cached
   preview into `MultiShareArgs.withLinkPreview`. Strip generic-web thumbnails
   at submission, and refresh/cancel visible pending previews when the preference
   changes. Do not remove received images or change stored message history.
   `ConversationFragment.sendMessage()` also accepts explicitly supplied previews
   instead of the view-model result. Decide how editing and intentional forwards
   fit the new-composition scope, and test those paths without rewriting already
   submitted send jobs. Omit a preview if stripping its image leaves no usable
   text instead of displaying an empty card.
6. Add request-count tests proving zero optional image requests when disabled;
   positive default-on and metadata-only cases; stale callback, restored-state
   and settings-toggle regressions; and assertions for conversation, story and
   share-sheet submissions. Extend `ChatsSettingsViewModelTest` and
   `ChatsSettingsScreenTest`, and inspect the chosen screen on API 35 before
   merging, as the app workflow requires.

Treat this as one independently reviewable app feature after the location is
chosen. A title-only network test by itself does not establish text-only sends.

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
their individual ledger entries; they need not depend on or re-import the
historical patch forest. After a PR merges, record its actual app SHA and move
that entry from `proposed` to `adopted` in a small engineering follow-up.
