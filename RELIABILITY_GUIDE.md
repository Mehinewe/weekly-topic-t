# Reliability and recovery

## Structure

Root scripts remain stable command entry points. `automation/shared/` owns
Telegram posting helpers, environment loading, and atomic file replacement.
Wednesday no longer imports Monday's command script. Keep feature decisions out
of shared infrastructure. Award selection, participation strike transitions,
and reminder targeting now live in their own `automation/<feature>/rules.py`
modules. Root scripts still own orchestration, I/O, and compatible commands.

## Checks

Run `python -m unittest discover -s tests -v` and `python validate_project.py`.
The offline checks workflow runs these on pushes and pull requests without
Telegram secrets. To enforce merge blocking, configure the checks job as a
required branch protection check in GitHub. Tests use temporary state and mocked
Telegram calls. Validation covers Monday and Wednesday content, award templates
and artwork, and reminder/evaluation settings and required message templates.
Award, reminder, and evaluation previews do not look up members or photos online.

## State ownership

`log_activity.py` is the only scheduled Telegram update poller. Do not add a
second poller. Monday, Wednesday, awards, and participation writers share the
`telegram-state` concurrency group. Awards and Monday share `sent_log.csv`;
Monday and Wednesday also write `topic_posts.csv`.
Atomic replacement protects one file against interrupted writes; it is not a
transaction across multiple files or a substitute for writer serialization.
Existing state paths and schemas are unchanged. Two new tracked JSON journals,
`topic_delivery.json` and `award_delivery.json`, store a hash of delivery inputs
and per-step status/message IDs. They contain no captions, names, or avatars.
The initial empty objects are intentional; missing or corrupt journals halt
delivery. Keep these journals when deploying subsequent code changes.

## Recovering a failed run

1. Read the failing Actions step and inspect the actual Telegram post before
   rerunning any delivery. A network timeout can mean the send succeeded but
   its response was lost. Automatic retries cannot guarantee exactly-once sends.
2. For corrupt state, pause the affected workflow and identify the last valid
   version in Git history. Compare newer successful posts before restoring only
   the affected file. Never reset all state or the activity cursor to empty.
3. Wednesday stores successful phase IDs and its answer snapshot. Keep them when
   repairing other state. Monday records completed delivery before pinning; if
   pinning fails, pin the existing message manually. Do not use --force to fix a
   pin, since it sends another post.
4. If sending succeeded but the state commit/push failed, reconcile the sent log
   with the actual chat before rerunning. Wednesday, Monday, and awards attempt state
   persistence even when a later step fails.
5. Run offline tests and relevant dry-run previews before re-enabling the job.

## Resuming Monday and awards

A successful send is checkpointed immediately. Confirmed Telegram rejections
leave that step retryable. Completed photos, follow-up text, and awards are
skipped when the same run resumes. All award assets must exist before delivery.
Input hashes prevent quietly continuing with changed winners, captions, artwork,
or target chat; restore the original inputs before resuming an unfinished run.

A `pending` step means a send may have happened (including a crash before the
request). It blocks retries, even with `--force`. Pause that workflow and inspect
the intended chat. Then edit only that step in its delivery journal:

- If delivered, replace its value with `{"status": "sent", "message_id": 123}`,
  using the actual Telegram message ID.
- If confirmed not delivered, delete only that pending step so it can retry.
- If still uncertain, leave it pending and investigate; do not clear the journal.

Commit the reconciled state before rerunning Actions. Do not change the plan
hash. Monday keys are `schedule.csv:YYYY-MM-DD`, using the target Monday; award
keys use the awarded week's Monday. Step names identify `photo`, `text`, or the
award key followed by `:badge` / `:text`. An award's `:complete` marker is internal
and is written after its delivery steps complete.

`--force` deliberately starts a fresh attempt when there are no pending steps;
it can duplicate successful deliveries, so it is not a recovery command.
A runner lost before its journal is pushed can still lose its latest checkpoint.
Inspect the chat after failed state persistence before any repeat run.

## Remaining work

Extract feature modules incrementally behind the unchanged root commands.
Participation evaluation still needs durable per-member action recovery before
it can safely resume arbitrary mid-run failures. Wednesday retains its existing
phase checkpoints rather than using the new pending-step journal. Add feature
switches and richer job summaries as those modules are separated.
