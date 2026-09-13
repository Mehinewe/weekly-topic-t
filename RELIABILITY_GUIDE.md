# Reliability and recovery

## Structure

Root scripts remain stable command entry points. `automation/shared/` owns
Telegram posting helpers, environment loading, and atomic file replacement.
Wednesday no longer imports Monday's command script. Keep feature decisions out
of shared infrastructure. Extract further feature modules incrementally with
regression tests; awards and participation are not fully separated yet.

## Checks

Run `python -m unittest discover -s tests -v` and `python validate_project.py`.
The offline checks workflow runs these on pushes and pull requests without
Telegram secrets. To enforce merge blocking, configure the checks job as a
required branch protection check in GitHub. Tests use temporary state and mocked
Telegram calls. Validation currently covers Monday and Wednesday content.

## State ownership

`log_activity.py` is the only scheduled Telegram update poller. Do not add a
second poller. Monday and Wednesday share the `telegram-state` concurrency group
with participation jobs because they update `topic_posts.csv`.
Atomic replacement protects one file against interrupted writes; it is not a
transaction across multiple files or a substitute for writer serialization.
Existing state paths and schemas are unchanged.

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
   with the actual chat before rerunning. Wednesday and Monday attempt state
   persistence even when a later step fails.
5. Run offline tests and relevant dry-run previews before re-enabling the job.

## Remaining work

Add per-message checkpoints for Monday split captions and multi-award runs;
currently a partial delivery can still duplicate on retry. Extend failure tests
and validation to awards, reminders, and evaluation before extracting those
features. Introduce feature switches and richer job summaries incrementally.
No automatic retry of ambiguous sends is introduced by this change.
