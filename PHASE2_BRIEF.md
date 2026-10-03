# Couponbase backend: Phase 2 brief (read fully at the start of every session)

## Goal
Phase 1 records every outbound click (`ClickEvent`) and appends a unique `clickref` to the
affiliate link. Phase 2 closes the loop: pull sales back from Awin, match each sale to the
click that caused it, and show earnings per click by store/source/coupon.

## What already exists (do not rebuild; reuse)
- `app/models/click.py` `ClickEvent`: id, clickref (unique, 32 hex), coupon_id, product_id,
  store_id (all nullable, SET NULL), src, network, visitor_hash (nulled after 90 days),
  is_bot, created_at.
- `app/services/affiliate.py`: `build_outbound_url`. Awin gets `?clickref=<ref>`.
- `app/services/click_service.py`: `record_click`, `scrub_visitor_hashes`.
- `app/models/store.py`: `affiliate_network` (string: none/direct/awin/cuelinks/admitad/impact),
  `link_template`, `cookie_days`.
- `app/models/source.py` `Source`: `kind` (enum, includes `awin`), `config` (JSON dict with
  `api_key` and `publisher_id` for Awin), `is_enabled`. **Awin credentials live in these rows.
  Reuse them. Do not add new env vars for Awin credentials.**
- `app/ingestion/sources/awin.py` `AwinSource`: the pattern to copy for HTTP (httpx, Bearer
  auth, `User-Agent`, timeout, injectable `httpx.AsyncClient` for tests).
- `app/ingestion/retry.py` `with_retries(fn, attempts=, base_delay=, retryable=)`.
- `app/services/scheduler.py`: `JOBS` dict, `_LOCK_*` advisory-lock ids, `build_scheduler()`,
  `job_interval_seconds()`. The Phase 1 job `scrub_clicks` is the template for a new job.
  Registering a job also requires: `app/routers/api/v1/system.py` `_STALE_AFTER`,
  `app/admin/views.py` JobRun filter values, and a `Settings` interval in `app/core/config.py`.
- `app/core/metrics.py`: Prometheus metrics, all prefixed `couponbase_`.
- Admin UI is sqladmin: `app/admin/views.py` (`StaffModelView`, `ALL_VIEWS`).
- API routers: `app/routers/api/v1/*.py`; admin-only endpoints use
  `Depends(require_role(Role.admin, Role.editor))` (see `ads.py`).
- Latest alembic revision: `a1c9e3f7b251` (head). New migration must set
  `down_revision = "a1c9e3f7b251"`.

## Awin Publisher API facts (verified against Awin's docs, October 2026)
- `GET https://api.awin.com/publishers/{publisherId}/transactions/`
- Auth: same Bearer token the existing `AwinSource` already sends.
- Required query params: `startDate`, `endDate` as `yyyy-MM-ddTHH:mm:ss`, and `timezone`
  (use `UTC`). **Maximum range between startDate and endDate is 31 days**, so longer
  lookbacks must be split into windows of 31 days or less.
- Optional: `dateType` = `transaction` (default) or `validation`, `status`
  (`pending|approved|declined|deleted`), `advertiserId`.
- Rate limit: roughly 20 calls per minute. Keep calls sequential, no fan-out.
- Each transaction includes: `id`, `transactionDate`, `clickDate`, `validationDate` (may be
  null), `advertiserId`, `commissionStatus`, `declineReason`, `commissionAmount`,
  `saleAmount`, and `clickRefs` (an object; our reference is under the key `clickRef`,
  others are `clickRef2`..`clickRef6`).
- **`saleAmount` and `commissionAmount` are documented both as plain numbers and as objects
  like `{"amount": 59.9, "currency": "GBP"}`. The parser must accept both.**
- A sale's status changes over weeks (pending -> approved/declined). So the sync must
  re-read recent history on every run and update existing rows, not only insert.

## Hard rules
1. Money is `Decimal` / `Numeric(12, 2)`. Never `float` for stored money.
2. Never log or put in an exception message: `api_key`, `Authorization`, full config.
3. Tests never touch the network. Use `httpx.MockTransport` (see tests/test_feed_and_retry.py).
4. A failure in one Awin source must not stop the others. Collect errors, keep going, and
   raise at the end only if every source failed.
5. Idempotent: running the sync twice in a row must change nothing the second time.
6. Do not sum money across currencies anywhere. Group by currency.
7. Migrations: reversible, no inline `sa.Enum` (use `String`), single alembic head.
   `tests/test_migrations_reversible.py` must keep passing.
8. Brand is **Couponbase** (metrics `couponbase_*`, env `COUPONBASE_*`). Never write "Couponza".
9. Do not touch: Phase 1 redirect behaviour, `affiliate.py` link building, existing tests'
   expectations. Add tests; do not weaken existing ones.
10. After every task run, from the repo root with the venv active:
    `ruff check <files you touched> && ruff format <files you touched> && pytest -q`
    All tests must pass before you commit. (A few unrelated ruff errors exist in
    tests/test_feed_and_retry.py; ignore those, do not fix them.)
11. One commit per task, message `phase2 <task id>: <summary>`. Tick the task in
    PHASE2_TASKS.md in the same commit.
12. Prefer small targeted edits over rewriting files. Read a file before editing it.