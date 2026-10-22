
Only after build passes implementation, gap-analysis, and review, publish
according to PR mode {{pr_mode}}: `draft` opens or updates a draft PR; `ready`
opens or updates a ready-for-review PR. Resolve the authenticated actor through
`{{pack_root}}/assets/scripts/github_api.py actor` and reuse an existing PR only
when the workflow marker, repo/base, authenticated author, and requested mode
are compatible. If a matching marker belongs to another author, record
`foreign_pr_exists` and ask the human how to proceed. V0 never merges.

**Ready gate (gc-0b5dqe, same hazard as gc-zb2a13).** `ready` never means
"open the pull request ready". The build's earlier pushes are `[skip ci]`
checkpoints, so a branch head can carry no workflow run at all, and a pull
request opened ready on such a head has zero check runs, its required contexts
stay pending forever and its merge state stays BLOCKED
(`fernanja/ascent_app#2786`). This gate applies only to a pull request this
workflow owns: one it opened, or one it reuses under the protocol below. When
`foreign_pr_exists` applies, skip this gate entirely and go to that protocol:
never mark ready, never `--undo`, and never push commits onto someone else's
pull request. For both modes, open or update the pull request as
a **draft** first, then:

1. Resolve the head sha the pull request points at (`gh pr view <number> --json headRefOid`)
   and confirm it equals the branch tip you published.
2. The head commit's message carries no skip-CI tag: `git log -1 --format=%B <sha>`
   (subject AND body) has none of `[skip ci]`, `[ci skip]`, `[no ci]`,
   `[skip actions]`, `[actions skip]`. The pull request title and body carry none
   either (the repository squashes with them, so a tag would skip the default
   branch's push workflows); strip one you find with `gh pr edit <number>
   --title/--body-file` before going on.
3. At least one workflow run exists for that sha:
   `gh run list --commit <sha> --json databaseId,event,status,conclusion --limit 10`
   is not empty. When the head is tagged or has zero runs (and the repository
   has workflows: `gh api repos/<owner>/<repo>/actions/workflows --jq .total_count`
   is not 0), put ONE empty commit on top of it in your own scratch worktree,
   `git commit --allow-empty -m "chore: run CI (<workflow-root-id>)"`, push it
   (the tree must not change: `git diff --quiet <sha> <tip>`), and use the new
   tip as the head. Never more than one such commit per publish; if the new tip
   still shows no run after the wait, fail the step: `gc.outcome=fail`,
   `gc.failure_class=hard`, and a `gc.failure_reason` naming the head sha.
   Provision that scratch worktree before the push (gc-ogmkzd): when
   `make -n worktree-deps` succeeds in it, run `make worktree-deps` there with
   one retry after 30 seconds, and when it still fails, fail the step
   (`gc.outcome=fail`, `gc.failure_class=hard`, reason naming `make
   worktree-deps` and its exit code). A fresh worktree without dependencies
   fails the pre-push hooks (`eslint: command not found`).
4. Wait for CI on the head (`gh pr checks <number> --watch --interval 30`, time
   bounded), then judge it with the pinned gate script in manual mode:
   `{{pack_root}}/assets/scripts/checks/pr-ci-green.sh --repo <owner>/<repo> --pr <number> --commit <sha>`
   (the pack root is the directory `gc formula list --json` lists for this
   pack; never a path relative to the rig).
5. Only when it printed `PASS` for the pull request's current head, and
   `pr_mode` is `ready`, run `gh pr ready <number>`. "No failures", "no checks
   reported" and a judge that was not run are not green. `pr_mode=draft` stops
   at the draft. A pull request already found open and ready on a head without
   `PASS` is a defect to repair now: `gh pr ready <number> --undo`, then run
   this gate again. On a red required check leave the pull request a draft and
   record the failing check names and URLs on the workflow root; do not rerun CI
   blind.

The one exception is the judge's own `Skip` for a repository with no workflows
at all; name it in the close reason.

When `foreign_pr_exists` applies, use the passive wait + mail human gate
pattern before mutating GitHub. This is not a timeout-driven task.

1. Before waiting, update workflow root metadata with:
   - `gc.github.publish_gate=waiting-human`
   - `gc.github.publish_gate_reason=foreign_pr_exists`
   - `gc.github.publish_gate_bead_id=<this bead id>`
   - preserve any existing `gc.github.publish_gate_mail_sent=true`
2. Park the current session so idle handling does not recycle it while the
   human decides:
   ```bash
   SESSION_TARGET="${GC_SESSION_ID:-${GC_SESSION_NAME:-}}"
   SESSION_ATTACH="${GC_SESSION_NAME:-$SESSION_TARGET}"
   WAIT_NOTE="Waiting for human decision on foreign GitHub PR for bead $GC_BEAD_ID."
   if [ -n "$SESSION_ATTACH" ]; then
     WAIT_NOTE="$WAIT_NOTE Resume with: gc session attach $SESSION_ATTACH"
   fi
   if [ -n "$SESSION_TARGET" ] && ! gc wait list --session "$SESSION_TARGET" | grep -Fq "$WAIT_NOTE"; then
     gc session wait "$SESSION_TARGET" \
       --sleep \
       --on-beads "$GC_BEAD_ID" \
       --note "$WAIT_NOTE"
   fi
   ```
3. If workflow root metadata does not already have
   `gc.github.publish_gate_mail_sent=true`, send exactly one mail with
   `gc mail send human ...`. Include the existing PR URL, existing PR author,
   authenticated actor, requested PR mode, workflow root id, this bead id, and
   requested response options: reuse existing PR, open a new PR, or abort. After
   sending, update workflow root metadata with
   `gc.github.publish_gate_mail_sent=true` and
   `gc.github.publish_gate_mail_to=human`.
4. Wait for explicit human feedback from the active session or mail thread. If
   the session idles, detaches, or restarts before the human responds, do not
   close this bead. A resumed worker must read the gate metadata and continue
   waiting from this gate.

Use `gc.github.publish_gate=approved` only after an explicit human decision and
record the selected publish action. Close fail only for explicit rejection or
abort, not for silence.
