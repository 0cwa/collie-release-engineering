# Inert application publication layer

Do not edit the current application README to advertise an unpublished feed.
After actual production DNS/feed/index/certificate readback and isolated official
F-Droid plus Droidify add/refresh/install/update acceptance, complete the separate
link-only activation snapshot. Freeze its externally approved raw
`link-activation.json` SHA256, then run:

```
python distribution/readme_badge.py --readme APP/README.md \
  --record VERIFIED/link-activation.json --approved-record-sha256 APPROVED_SHA256 \
  --output NEW_PATCH_DIRECTORY
```

The helper leaves APP untouched and produces a reviewable README.patch plus the
unchanged official badge/license assets. Apply them in a later ordinary reviewed
application PR/publication layer. The F-Droid image goes immediately after the
existing Obtainium anchor, at the same height80; Matrix, donations, branding and
all other content remain. It links to our custom repository landing page, never
an invented official package entry. Do not merge/apply the patch on test-key,
staging, preview-only or unknown evidence. No badge is added by merging this
engineering preparation. Source maintainers can carry the helper/asset reference
through the later publication handoff; it is not part of the Android build.
