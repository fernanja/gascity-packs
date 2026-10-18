
If push is {{push}}, use create-if-absent or expected-object-id update only.
Fail closed if the remote cannot enforce atomic or lease-checked semantics.

Reviewer-first (gc-gef2p8): push the approved branch head WITHOUT a `[skip ci]`
(or `[ci skip]`, `[no ci]`, `[skip actions]`, `[actions skip]`) tag, so that CI
runs once, on the approved commit. When the head commit's own message carries
the tag, put an empty commit `chore: run CI` on top of it and push that; the
tree must not change. Record the pushed sha as `gc.build.publish_pushed_commit`
on the workflow root.
