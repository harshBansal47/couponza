import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.core.security import create_access_token
from app.main import app

# Ensure every model is registered on Base.metadata before create_all runs.
from app.models import ad as _ad  # noqa: F401
from app.models import category as _category  # noqa: F401
from app.models import coupon as _coupon  # noqa: F401
from app.models import ingestion_run as _ingestion_run  # noqa: F401
from app.models import page as _page  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models import store as _store  # noqa: F401
from app.models import user as _user  # noqa: F401
from app.models.user import Role
from app.schemas.user import UserCreate
from app.services import user_service

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter's storage is a process-wide singleton; without this, tests
    that hit /register, /login, or /verify enough times would rate-limit
    each other in ways that have nothing to do with what each test is
    actually checking."""
    limiter.reset()
    yield
    limiter.reset()


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores foreign keys unless told otherwise; Postgres always enforces them.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def async_session_maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(async_session_maker):
    async def _override_get_db():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_headers(async_session_maker):
    """Factory: `await make_headers(Role.admin)` -> {"Authorization": "Bearer ..."}."""

    async def _make(role: Role = Role.user) -> dict[str, str]:
        email = f"{role.value}-{uuid.uuid4().hex[:8]}@example.com"
        async with async_session_maker() as session:
            user = await user_service.create_user(
                session, UserCreate(email=email, password="supersecret123"), role=role
            )
            token = create_access_token(str(user.id), user.role.value)
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest_asyncio.fixture
async def admin_headers(make_headers):
    return await make_headers(Role.admin)


@pytest_asyncio.fixture
async def mcp_client(async_session_maker):
    """A CouponzaClient pointed at the same in-memory test app, via ASGITransport —
    no real network needed, same pattern as the `client` fixture above."""
    from httpx import ASGITransport

    from mcp_server.client import CouponzaClient

    async def _override_get_db():
        async with async_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    yield CouponzaClient(base_url="http://testserver/api/v1", transport=ASGITransport(app=app))
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def editor_headers(make_headers):
    return await make_headers(Role.editor)


@pytest_asyncio.fixture
async def user_headers(make_headers):
    return await make_headers(Role.user)
