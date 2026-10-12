This is the `implementation-base` methodology contract source-anchor close
step.

Concrete methodology packs override this step only when they need additional
artifact checks. Verify the implementation result and close the source anchor
before this workflow step closes pass. If `{{summary_path}}` is set, write or
verify the per-item implementation summary there before closing. Whatever is
written there, including the handoff for an implementation that stopped, is a
`gc.build.implementation-summary.v1` artifact: front matter with `status:
blocked` when the work did not finish, never plain prose (gc-s95gvs). Validate
it with `../assets/scripts/checks/build-artifact-valid.sh` before closing.
