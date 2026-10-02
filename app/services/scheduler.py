"""The scheduled work Couponza runs on itself.

Replaces the host-level cron scripts. The reason is not elegance: with cron,
whether ingestion runs at all depends on somebody having configured a crontab
on a particular box, and the failure mode is silence — a coupon database that
quietly goes stale while every page still renders normally. Running the schedule
inside the process means "it stopped working" shows up in the health check and
in the job table.

Design decisions worth defending:

* **Advisory locks, held across the whole job.** With N replicas, a naive
  scheduler has every replica ingesting the same sources at the same moment.
  The lock must be *session-scoped* (`pg_try_advisory_lock`, not the `_xact_`
  variant) because a transaction-scoped lock is released the instant the
  transaction that took it commits — which would leave the actual work
  unguarded. It is explicitly unlocked in a `finally`, because SQLAlchemy
  returns the connection to the pool on close and a pool rollback does not
  release advisory locks.
* **Every attempt gets a row, failures included.** A job that records only its
  successes is indistinguishable from a job that stopped running. `skipped` is
  recorded too, so the history does not show a gap on every non-leader replica.
* **`max_instances=1` plus `coalesce=True`.** A sweep that overruns its own
  interval must not stack a second copy behind itself; the missed tick is
  dropped and the next one proceeds normally.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics, sentry
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.observability import get_logger
from app.models.job import JobRun, JobStatus
from app.services.alert_service import run_alert_scan
from app.services.lifecycle_service import expire_stale_coupons, refresh_coupon_prices
from app.services.verification_service import run_sweep as run_verification_sweep

logger = get_logger("couponza.scheduler")

# Postgres advisory locks take a signed 64-bit integer. Any stable constant
# works; these just need to be distinct from each other.
_LOCK_EXPIRE = 0x0C0FFEE0
_LOCK_REFRESH = 0x0C0FFEE1
_LOCK_ALERTS = 0x0C0FFEE2
_LOCK_VERIFY = 0x0C0FFEE3


# A job that fails is recorded and re-raised so the caller can distinguish "ran
# and broke" from "another replica did it". The failure row is written first
# either way, so the history is never missing an outcome.
async def _expire_job(db: AsyncSession) -> str:
    expired = await expire_stale_coupons(db)
    return f"expired {expired}"


async def _refresh_job(db: AsyncSession) -> str:
    refreshed = await refresh_coupon_prices(db)
    return f"refreshed {refreshed} source(s)"


async def _alerts_job(db: AsyncSession) -> str:
    sent = await run_alert_scan(db)
    return f"sent {sent} alert(s)"


async def _verify_job(db: AsyncSession) -> str:
    summary = await run_verification_sweep(db)
    return (
        f"examined {summary['examined']}, {summary['attempts']} attempt(s), "
        f"{summary['retired']} retired"
    )


JOBS: dict[str, tuple[Callable[[AsyncSession], Awaitable[str]], int]] = {
    "expire_coupons": (_expire_job, _LOCK_EXPIRE),
    "refresh_prices": (_refresh_job, _LOCK_REFRESH),
    "send_alerts": (_alerts_job, _LOCK_ALERTS),
    "verify_coupons": (_verify_job, _LOCK_VERIFY),
}


async def _try_lock(db: AsyncSession, lock_id: int) -> bool:
    """Take a session-scoped advisory lock. Caller must release it.

    Returns True when the caller may proceed. Advisory locks are a Postgres
    feature, so on any other dialect (the test suite runs SQLite) this returns
    True unconditionally: a single shared test connection cannot have two
    workers racing in the first place, and a hard failure here would mean the
    scheduler is untestable rather than wrong.
    """
    if db.bind is not None and db.bind.dialect.name != "postgresql":
        return True
    result = await db.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
    # Commit so the lock is held by this connection rather than being discarded
    # with the implicit transaction's result set.
    await db.commit()
    return bool(result.scalar())


async def _unlock(db: AsyncSession, lock_id: int) -> None:
    if db.bind is not None and db.bind.dialect.name != "postgresql":
        return
    try:
        await db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
        await db.commit()
    except SQLAlchemyError:
        # If the connection is already gone, Postgres released the lock when the
        # backend exited. Nothing to do, and nothing worth failing the job over —
        # the worst case is a log line that says so.
        logger.warning("advisory lock release failed", extra={"lock_id": lock_id})


def _row(job: str, status: JobStatus, detail: str | None, started: datetime) -> JobRun:
    finished = datetime.now(UTC)
    return JobRun(
        job=job,
        status=status,
        # Truncated because an exception message can carry a whole HTML page.
        detail=detail[:2000] if detail else None,
        started_at=started,
        finished_at=finished,
        duration_ms=int((finished - started).total_seconds() * 1000),
    )


async def run_job(name: str) -> str:
    """Execute one scheduled job under its advisory lock.

    Raises on failure so the caller can distinguish "ran and broke" from
    "another replica did it"; the failure is recorded first either way.
    """
    fn, lock_id = JOBS[name]
    async with AsyncSessionLocal() as db:
        if not await _try_lock(db, lock_id):
            # Losing this race is the normal state of affairs on every replica
            # that is not currently doing the work. Not an error.
            db.add(_row(name, JobStatus.skipped, "another worker held the lock", datetime.now(UTC)))
            await db.commit()
            metrics.job_runs.labels(name, JobStatus.skipped.value).inc()
            logger.info("job skipped (lock held elsewhere)", extra={"job": name})
            return "skipped"

        started = datetime.now(UTC)
        try:
            detail = await fn(db)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            db.add(_row(name, JobStatus.failed, message, started))
            await db.commit()
            metrics.job_runs.labels(name, JobStatus.failed.value).inc()
            metrics.job_duration.labels(name).observe((datetime.now(UTC) - started).total_seconds())
            logger.exception("scheduled job failed", extra={"job": name})
            # Scheduled work has no request to hang an exception off, so report
            # it explicitly — otherwise a job that has been failing for a week
            # is invisible outside the database.
            sentry.capture_exception(exc, job=name)
            raise
        else:
            db.add(_row(name, JobStatus.success, detail, started))
            await db.commit()
            elapsed = (datetime.now(UTC) - started).total_seconds()
            metrics.job_runs.labels(name, JobStatus.success.value).inc()
            metrics.job_duration.labels(name).observe(elapsed)
            logger.info(
                "scheduled job finished",
                extra={"job": name, "detail": detail, "duration_s": round(elapsed, 2)},
            )
            return detail
        finally:
            await _unlock(db, lock_id)


async def _fire(name: str) -> None:
    """APScheduler entry point.

    `run_job` already records the failure and logs it with a traceback, and
    APScheduler logs unhandled job errors itself with the job id attached. A
    second try/except here would only add a bare `pass` over an already-logged
    failure.
    """
    await run_job(name)


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    # Start a little after boot so startup never contends with migrations.
    offset = 30

    scheduler.add_job(
        _fire,
        IntervalTrigger(hours=settings.expire_interval_hours),
        args=["expire_coupons"],
        id="expire_coupons",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=offset),
        replace_existing=True,
    )
    scheduler.add_job(
        _fire,
        IntervalTrigger(hours=settings.ingestion_interval_hours),
        args=["refresh_prices"],
        id="refresh_prices",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=offset + 30),
        replace_existing=True,
    )
    scheduler.add_job(
        _fire,
        IntervalTrigger(minutes=settings.alert_scan_interval_minutes),
        args=["send_alerts"],
        id="send_alerts",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=offset + 60),
        replace_existing=True,
    )
    scheduler.add_job(
        _fire,
        IntervalTrigger(hours=settings.verification_interval_hours),
        args=["verify_coupons"],
        id="verify_coupons",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=offset + 90),
        replace_existing=True,
    )
    return scheduler


_scheduler: AsyncIOScheduler | None = None


async def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled (SCHEDULER_ENABLED is off)")
        return None
    if _scheduler is not None:
        return _scheduler

    _scheduler = build_scheduler()
    _scheduler.start()
    logger.info(
        "scheduler started",
        extra={
            "expire_every_hours": settings.expire_interval_hours,
            "ingest_every_hours": settings.ingestion_interval_hours,
            "alerts_every_minutes": settings.alert_scan_interval_minutes,
        },
    )
    return _scheduler


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        # wait=False: on shutdown we would rather abandon an in-flight job than
        # hang the process past the orchestrator's kill timeout.
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def run_job_now(name: str) -> str:
    """Run a job immediately, ignoring its schedule but honouring the lock.

    Backs the admin "run now" button and gives tests a deterministic sweep
    without waiting on an interval.
    """
    if name not in JOBS:
        raise ValueError(f"unknown job: {name}")
    return await run_job(name)


async def last_run(db: AsyncSession, name: str) -> JobRun | None:
    result = await db.execute(
        select(JobRun)
        .where(JobRun.job == name, JobRun.status != JobStatus.skipped)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def recent_runs(db: AsyncSession, limit: int = 50) -> list[JobRun]:
    result = await db.execute(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit))
    return list(result.scalars().all())


__all__ = [
    "JOBS",
    "build_scheduler",
    "last_run",
    "recent_runs",
    "run_job",
    "run_job_now",
    "start_scheduler",
    "stop_scheduler",
]
