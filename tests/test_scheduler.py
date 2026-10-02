"""The in-process scheduler and the job ledger.

The behaviour worth protecting here is that *failures are recorded*. A job that
only writes a row on success is indistinguishable from a job that stopped
running six weeks ago — the failure mode this whole table exists to prevent.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.category import Category
from app.models.coupon import Coupon, CouponStatus, DiscountType
from app.models.job import JobRun, JobStatus
from app.models.store import Store
from app.services import scheduler


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch, async_session_maker):
    """Point the scheduler at the in-memory test database.

    `run_job` opens its own session from the module-level `AsyncSessionLocal`, so
    without this every test would write job rows into the real Postgres and then
    assert against an empty in-memory database.
    """
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", async_session_maker)


async def _rows(async_session_maker, job: str | None = None) -> list[JobRun]:
    async with async_session_maker() as db:
        stmt = select(JobRun).order_by(JobRun.started_at.asc())
        if job:
            stmt = stmt.where(JobRun.job == job)
        return list((await db.execute(stmt)).scalars().all())


# ---- Registry ----


def test_every_scheduled_job_has_a_distinct_lock() -> None:
    # Two jobs sharing a lock id would silently serialise against each other, so
    # one would record "skipped" for reasons that look like a scheduling bug.
    lock_ids = [lock for _fn, lock in scheduler.JOBS.values()]
    assert len(set(lock_ids)) == len(lock_ids)


def test_all_known_jobs_are_registered_with_the_scheduler() -> None:
    scheduled = {job.id for job in scheduler.build_scheduler().get_jobs()}
    assert scheduled == set(scheduler.JOBS)


def test_scheduler_uses_the_configured_intervals() -> None:
    # Asserted against the trigger's timedelta rather than its string form,
    # which drops leading zeroes: "interval[6:00:00]", not "[0:06:00:00]".
    jobs = {job.id: job for job in scheduler.build_scheduler().get_jobs()}
    assert jobs["expire_coupons"].trigger.interval == timedelta(hours=1)
    assert jobs["refresh_prices"].trigger.interval == timedelta(hours=6)
    assert jobs["send_alerts"].trigger.interval == timedelta(minutes=30)
    assert jobs["verify_coupons"].trigger.interval == timedelta(hours=12)


@pytest.mark.asyncio
async def test_run_now_rejects_an_unknown_job() -> None:
    with pytest.raises(ValueError, match="unknown job"):
        await scheduler.run_job_now("no_such_job")


# ---- Execution ----


@pytest.mark.asyncio
async def test_successful_run_records_one_row(async_session_maker) -> None:
    result = await scheduler.run_job("expire_coupons")
    assert "expired" in result

    rows = await _rows(async_session_maker, "expire_coupons")
    assert len(rows) == 1
    assert rows[0].status is JobStatus.success
    assert rows[0].duration_ms is not None
    assert rows[0].finished_at is not None


@pytest.mark.asyncio
async def test_failed_run_is_recorded_and_re_raised(async_session_maker, monkeypatch) -> None:
    async def _boom(db):
        raise RuntimeError("adapter exploded")

    monkeypatch.setitem(
        scheduler.JOBS, "expire_coupons", (_boom, scheduler.JOBS["expire_coupons"][1])
    )

    with pytest.raises(RuntimeError, match="adapter exploded"):
        await scheduler.run_job("expire_coupons")

    rows = await _rows(async_session_maker, "expire_coupons")
    assert len(rows) == 1
    assert rows[0].status is JobStatus.failed
    # The exception type is in the detail, so the history says what broke
    # without needing the (rotated) log.
    assert "RuntimeError: adapter exploded" in (rows[0].detail or "")


@pytest.mark.asyncio
async def test_failure_detail_is_truncated(async_session_maker, monkeypatch) -> None:
    # An exception message can carry a whole HTML page from a broken adapter.
    async def _boom(db):
        raise ValueError("x" * 5000)

    monkeypatch.setitem(
        scheduler.JOBS, "expire_coupons", (_boom, scheduler.JOBS["expire_coupons"][1])
    )
    with pytest.raises(ValueError):
        await scheduler.run_job("expire_coupons")

    row = (await _rows(async_session_maker, "expire_coupons"))[0]
    assert len(row.detail) <= 2000


@pytest.mark.asyncio
async def test_lock_loss_is_recorded_as_skipped_not_failed(
    async_session_maker, monkeypatch
) -> None:
    # Losing the race is the normal state on every replica that is not the one
    # currently working. Recording it as a failure would make a healthy
    # three-replica deployment look permanently broken.
    ran: list[str] = []

    async def _never(db):
        ran.append("ran")
        return "done"

    async def _no_lock(db, lock_id):
        return False

    monkeypatch.setitem(scheduler.JOBS, "send_alerts", (_never, scheduler.JOBS["send_alerts"][1]))
    monkeypatch.setattr(scheduler, "_try_lock", _no_lock)

    assert await scheduler.run_job("send_alerts") == "skipped"
    assert ran == []

    row = (await _rows(async_session_maker, "send_alerts"))[0]
    assert row.status is JobStatus.skipped


@pytest.mark.asyncio
async def test_advisory_lock_is_released_even_when_the_job_raises(
    async_session_maker, monkeypatch
) -> None:
    # If the unlock were skipped on the failure path, one bad tick would wedge
    # the job permanently — it would record `skipped` forever.
    released: list[int] = []

    async def _boom(db):
        raise RuntimeError("nope")

    async def _record_unlock(db, lock_id):
        released.append(lock_id)

    monkeypatch.setitem(
        scheduler.JOBS, "refresh_prices", (_boom, scheduler.JOBS["refresh_prices"][1])
    )
    monkeypatch.setattr(scheduler, "_unlock", _record_unlock)

    with pytest.raises(RuntimeError):
        await scheduler.run_job("refresh_prices")

    assert released == [scheduler.JOBS["refresh_prices"][1]]


@pytest.mark.asyncio
async def test_run_now_ignores_the_schedule(async_session_maker) -> None:
    # The admin "run now" button needs a deterministic sweep, not one that waits
    # on an interval.
    assert "expired" in await scheduler.run_job_now("expire_coupons")


# ---- History queries ----


@pytest.mark.asyncio
async def test_last_run_ignores_skipped_rows(async_session_maker, monkeypatch) -> None:
    # A replica that never wins the lock must not make the health check report
    # a job as never having run.
    async with async_session_maker() as db:
        db.add_all(
            [
                JobRun(
                    job="send_alerts",
                    status=JobStatus.skipped,
                    started_at=datetime.now(UTC),
                    detail="another worker held the lock",
                ),
            ]
        )
        await db.commit()

    async def _alerts(db):
        return "sent 3 alert(s)"

    monkeypatch.setitem(scheduler.JOBS, "send_alerts", (_alerts, scheduler.JOBS["send_alerts"][1]))
    await scheduler.run_job("send_alerts")

    async with async_session_maker() as db:
        row = await scheduler.last_run(db, "send_alerts")
        assert row.status is JobStatus.success


@pytest.mark.asyncio
async def test_last_run_is_none_before_anything_has_run(async_session_maker) -> None:
    async with async_session_maker() as db:
        assert await scheduler.last_run(db, "send_alerts") is None


@pytest.mark.asyncio
async def test_recent_runs_are_newest_first(async_session_maker) -> None:
    await scheduler.run_job("expire_coupons")
    await scheduler.run_job("expire_coupons")

    async with async_session_maker() as db:
        rows = await scheduler.recent_runs(db, limit=10)
        assert len(rows) == 2
        assert rows[0].started_at >= rows[1].started_at


@pytest.mark.asyncio
async def test_recent_runs_respects_the_limit(async_session_maker) -> None:
    await scheduler.run_job("expire_coupons")
    await scheduler.run_job("expire_coupons")

    async with async_session_maker() as db:
        assert len(await scheduler.recent_runs(db, limit=1)) == 1


# ---- Lifecycle ----


@pytest.mark.asyncio
async def test_scheduler_is_off_unless_enabled(monkeypatch) -> None:
    # Off by default so tests and one-off containers never start background work.
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.scheduler_enabled is False
    monkeypatch.setattr(settings, "scheduler_enabled", False, raising=False)
    assert await scheduler.start_scheduler() is None


@pytest.mark.asyncio
async def test_start_is_idempotent(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "scheduler_enabled", True, raising=False)
    try:
        first = await scheduler.start_scheduler()
        second = await scheduler.start_scheduler()
        assert first is second
        assert len(first.get_jobs()) == len(scheduler.JOBS)
    finally:
        await scheduler.stop_scheduler()


@pytest.mark.asyncio
async def test_stop_is_safe_when_never_started() -> None:
    await scheduler.stop_scheduler()  # must not raise


@pytest.mark.asyncio
async def test_stop_clears_the_module_level_scheduler(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "scheduler_enabled", True, raising=False)
    await scheduler.start_scheduler()
    await scheduler.stop_scheduler()
    assert scheduler._scheduler is None


@pytest.mark.asyncio
async def test_expire_job_expires_stale_coupons(async_session_maker) -> None:
    async with async_session_maker() as db:
        store = Store(name="Store", slug=f"store-{uuid.uuid4().hex[:6]}")
        category = Category(name="Cat", slug=f"cat-{uuid.uuid4().hex[:6]}")
        db.add_all([store, category])
        await db.flush()
        coupon = Coupon(
            title=f"Deal {uuid.uuid4().hex[:6]}",
            slug=f"deal-{uuid.uuid4().hex[:8]}",
            code="SAVE",
            discount_type=DiscountType.percentage,
            discount_value=10.0,
            store_id=store.id,
            category_id=category.id,
            destination_url="https://example.com/deal",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        db.add(coupon)
        await db.commit()
        coupon_id = coupon.id

    assert "expired 1" in await scheduler.run_job("expire_coupons")

    async with async_session_maker() as db:
        row = await db.get(Coupon, coupon_id)
        assert row.status is CouponStatus.expired
