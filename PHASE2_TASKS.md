# Backend tasks (do ONLY the first unchecked task; tick it when done)

- [x] B1 Conversion model + migration
  - `app/models/conversion.py` `Conversion(Base, UUIDPKMixin, TimestampMixin)`:
    network String(20); network_txn_id String(64); clickref String(64) nullable, indexed;
    click_id Uuid FK click_events.id ondelete SET NULL nullable, indexed;
    store_id Uuid FK stores.id ondelete SET NULL nullable;
    status String(20) (pending|approved|declined|deleted);
    sale_amount Numeric(12,2) nullable; commission_amount Numeric(12,2) nullable;
    currency String(3) nullable; transaction_at, click_at, validated_at DateTime(tz) nullable;
    decline_reason String(200) nullable.
    UniqueConstraint(network, network_txn_id). Index on (store_id, transaction_at).
  - Export it from `app/models/__init__.py`.
  - Migration `alembic/versions/<new>_conversions.py`, `down_revision = "a1c9e3f7b251"`,
    with a working downgrade (drop indexes, then table).
  - Verify: `alembic heads` shows exactly one head; run
    `DATABASE_URL=postgresql+asyncpg://u:p@localhost/x SECRET_KEY=x alembic upgrade a1c9e3f7b251:head --sql`
    and read the SQL; `pytest -q` passes.

- [ ] B2 Awin transactions parser (pure functions, no network yet)
  - `app/services/revenue_sync.py`: dataclass `ParsedTransaction` (network, network_txn_id,
    clickref, status, sale_amount: Decimal|None, commission_amount: Decimal|None,
    currency, transaction_at, click_at, validated_at, decline_reason, advertiser_id).
  - `parse_awin_transaction(raw: dict) -> ParsedTransaction | None`: returns None (never
    raises) for malformed rows. Handle: amounts as number OR `{"amount","currency"}`;
    amounts as strings; `clickRefs` missing/empty/not a dict; our ref at
    `clickRefs["clickRef"]`; status lowercased and mapped, unknown status -> "pending";
    ISO datetimes with or without timezone (assume UTC if naive); null validationDate.
  - `date_windows(start, end, max_days=31) -> list[tuple[datetime, datetime]]`.
  - Tests `tests/test_revenue_parse.py`: both amount shapes, missing clickRefs, bad rows
    return None, status mapping, windows (exactly 31 days, 62 days, 1 day, start>end -> []).

- [ ] B3 Awin transactions client + upsert
  - In `revenue_sync.py`: `AwinTransactionsClient(client: httpx.AsyncClient | None = None)`
    with `fetch(config, start, end, date_type) -> list[dict]`. Reads `api_key` and
    `publisher_id` from the source config exactly like `AwinSource` (raise ValueError if
    missing, without printing the config). Calls the endpoint from the brief with
    `timezone=UTC`, Bearer auth, wrapped in `with_retries`. Response is a JSON list.
  - `upsert_transactions(db, parsed: list[ParsedTransaction]) -> Counter` with keys
    `created|updated|unchanged|unmatched`. Match `clickref` to `ClickEvent.clickref` to set
    `click_id` and `store_id` (store from the click; if no click match, `click_id` stays
    NULL and the row counts as `unmatched` but is STILL saved). Update an existing row
    (same network + txn id) only when a tracked field changed; otherwise `unchanged`.
    A later sync may match a previously unmatched row if the click now exists: handle it.
  - Tests `tests/test_revenue_upsert.py` using `httpx.MockTransport`: creates rows; second
    identical run -> all `unchanged`; status pending -> approved updates the row;
    unmatched clickref still saved with NULL click_id; wrong api_key (401) raises without
    the key appearing in the exception text.

- [ ] B4 `sync_awin_revenue` service + scheduler job
  - `async def sync_awin_revenue(db, *, client=None, now=None) -> dict` loops over
    `Source` rows with `kind == SourceKind.awin` and `is_enabled`. For each: two passes.
    Pass 1 `dateType=transaction` over the last `revenue_lookback_days` (new Setting,
    default 62) in <=31-day windows; pass 2 `dateType=validation` over the last 31 days
    (catches old sales that were just approved). Parse, upsert, commit per source.
    Per rule 4: one failing source is recorded in the summary and does not stop others;
    raise only if all enabled sources failed. Return counts + `sources_ok/sources_failed`.
  - Settings (`app/core/config.py`): `revenue_sync_interval_hours: int = 6`,
    `revenue_lookback_days: int = 62`.
  - Scheduler: add job `sync_revenue` exactly like `scrub_clicks`: new `_LOCK_SYNC_REVENUE
    = 0x0C0FFEE5`, `_sync_revenue_job`, entry in `JOBS`, hourly dict in
    `job_interval_seconds`, a trigger in `build_scheduler` (offset +150s), `_STALE_AFTER`
    entry (`timedelta(hours=24)`), and the JobRun filter value in `app/admin/views.py`.
    Job returns a short summary string.
  - Tests: two Awin sources where one returns 500 and one succeeds (the good one is saved,
    summary shows 1 ok / 1 failed); all sources failing raises; no Awin sources -> returns
    zero counts without error; `tests/test_scheduler.py` still passes unmodified.

- [ ] B5 Metrics + admin view
  - `app/core/metrics.py`: Counter `couponbase_revenue_sync_transactions_total` with label
    `outcome` (created|updated|unchanged|unmatched). Increment it from the sync.
  - `app/admin/views.py`: read-only `ConversionAdmin` (no create/edit), columns: status,
    commission_amount, sale_amount, currency, store_id, transaction_at, clickref,
    network_txn_id; sortable by transaction_at; filter by status. Register in `ALL_VIEWS`.
  - Test: the admin list page for Conversion renders for an admin session (copy the
    approach in `tests/test_admin.py`); the counter increases after a sync.

- [ ] B6 Earnings API
  - `GET /api/v1/admin/earnings` in a new router `app/routers/api/v1/earnings.py`,
    registered like the other routers, protected with
    `Depends(require_role(Role.admin, Role.editor))`.
  - Query: `days` (1-365, default 30), `group_by` in `store|src|coupon` (default `store`).
  - Response: list of rows, one per (group, currency):
    `{group_id, group_label, currency, clicks, conversions, approved_commission,
    pending_commission, declined_commission, epc, conversion_rate}`.
    `clicks` counts ClickEvent rows in the window with `is_bot = false`.
    `epc` = (approved + pending commission) / clicks, rounded to 4 places, null if 0 clicks.
    `conversion_rate` = conversions / clicks. Money as strings or Decimal-safe numbers,
    never float arithmetic on stored money.
    A row exists for every group with clicks, even with zero conversions.
    Unmatched conversions (no click) go in one row with `group_label = "unattributed"`.
  - Tests: 401/403 for non-admin; correct numbers for a seeded dataset (2 stores, mixed
    statuses, a bot click that must be excluded, two currencies kept separate);
    `group_by=src`; invalid `group_by` -> 422.

- [ ] B7 Docs + final check
  - README section "Revenue sync" (what runs, the setting names, how to read the admin
    Conversions page and `/admin/earnings`, the 31-day window rule, how statuses change).
  - Add a Prometheus alert in `ops/prometheus-rules.yml` for `sync_revenue` being stale >
    24h, copying the style of the `verify_coupons` rule.
  - Run the full `pytest -q`, `ruff check` on every file touched in Phase 2, and the
    alembic SQL generation from B1 once more. Write `PHASE2_SUMMARY.md`: files added,
    settings added, anything skipped and why, and manual steps (run `alembic upgrade head`,
    set `affiliate_network='awin'` on Awin-sourced stores, confirm one real sync).