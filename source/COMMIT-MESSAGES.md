# Review commit wording before source publication

`commit_messages.py` exports versioned JSON subjects/bodies and a complete Markdown preview without making Git objects or changing refs. `draft-commit-messages.json` is the current wording draft for the historical plan-v3, not authorization to publish or the final plan after security/native-origin updates. Keep the feature explicit with a restrained Collie voice. Edit all subjects/bodies in one batch or delegate that style work; do not truncate messages automatically.

```sh
python3 source/commit_messages.py --plan source/candidate-plan-v3.json \
  --json "$TASK_SCRATCH/messages.json" --markdown "$TASK_SCRATCH/messages.md"
```

The exporter exclusively creates both output names and refuses to overwrite existing files or symlinks. If the second name cannot be reserved or writing fails, it reports any paths already created and leaves them intact: inspect those partial drafts before removing them because they may contain owner edits. It never rolls back by deleting an exposed output path. Carry reviewed wording onto a changed plan deliberately, then regenerate the preview and approve both exact raw plan and message-file digests. Publication requires `--messages FILE --approved-messages-sha256 DIGEST`; the publisher consumes each exact subject/body and verifies layer order and visible provenance. No final G is generated while wording/dependencies are unresolved. `--historical-messages` explicitly reproduces old historical renderer behavior for existing candidates and fixtures; it is not the new publication default.

New messages show accurate composite/original source provenance without requiring development-process or tool attribution. Original commit objects and source plan provenance stay unchanged, even if their historical markers said AI-Generated. Importing an ordinary reviewed contributor commit preserves that original commit in integration ancestry; newly generated replay commits remain explicitly composite.
