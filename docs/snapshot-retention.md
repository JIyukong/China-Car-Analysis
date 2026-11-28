# SALES snapshot retention

`scheduler.snapshot_retention.cleanup(project_root, apply=False, protected_roots=(), now=None)` previews eligible snapshots without writing files. Pass `apply=True` only while holding both SALES chain State locks, or include every active chain snapshot root in `protected_roots`. Run once per daily scheduler tick; the integration owns lock acquisition and cadence.

The fixed roots are `pipelines/ingest/sales/crawl_16888_{city,style}/data/snapshots`. Only direct attempt folders below YYYY-MM periods (legacy YYYYMM also accepted) are considered. The newest directory/file modification time must be strictly more than 120 hours old. Linked/reparse ancestors and nested directories are rejected. Root CSVs and other businesses are outside scope.

Before unlinking direct files, a flushed and fsynced receipt under `logs/snapshot_retention` preserves the manifest, every filename/size/SHA256 and UTF-8 error/review/exclusion evidence. Files are rechecked against the receipt before deletion; unexpected changes fail closed. Empty period folders remain. Returns `eligible`, `deleted`, `skipped`, `errors`, and deleted `bytes`. Exceptions are recorded per attempt; failed captures without a manifest are eligible only when crawl.log ends with an explicit terminal exception; the last 4000 characters are retained. Invalid manifests are retained with an error.

Offline verification: `python -m pytest tests/test_snapshot_retention.py -q`. No database connection is used.

The scheduler registers a Beijing-time 02:30 daily job. Existing resident schedulers also pick up the once-per-day cleanup through their fresh `resume` subprocesses, so deployment does not require restarting the scheduler. The successful cleanup date is stored locally; an active collection causes cleanup to skip and retry on a later tick. Use `python -m scheduler.runner --cleanup-sales-snapshots --plan` to preview, or omit `--plan` to run immediately under the same locks. Status queries do not download data; source rechecks create fresh snapshots, `--from-csv` reuses files, and accepted publication recovery uses ODS.
