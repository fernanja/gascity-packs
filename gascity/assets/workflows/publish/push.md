
If push is {{push}}, use create-if-absent or expected-object-id update only.
Fail closed if the remote cannot enforce atomic or lease-checked semantics.

Reviewer-first (gc-gef2p8): push the approved branch head WITHOUT a `[skip ci]`
(or `[ci skip]`, `[no ci]`, `[skip actions]`, `[actions skip]`) tag, so that CI
runs once, on the approved commit. When the head commit's own message carries
the tag, put an empty commit `chore: run CI` on top of it and push that; the
tree must not change. The same empty commit applies when the remote already
holds an untagged head with zero workflow runs (`gh run list --commit <sha>` is
empty), so "already pushed" never skips the push of a head CI has not run on
(gc-zb2a13). Record the pushed sha as `gc.build.publish_pushed_commit`
on the workflow root.

Pushes survive a session restart (gc-20q5b1): when `git ls-remote origin
<branch>` already equals the approved commit and only the empty run-CI commit
is missing, create it through the GitHub API with the same tree and move the
branch ref (no local `git push`); run every other `git push` detached with
`nohup`, and after a restart re-verify the remote before any retry. The
recipes are in `assets/workflows/build-from-review-base/publish.md`.
