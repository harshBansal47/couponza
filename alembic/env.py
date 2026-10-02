import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.database import Base

# Import every model module here so its table is registered on Base.metadata
# before autogenerate compares it against the live database.
from app.models import ad as _ad  # noqa: F401
from app.models import base as _base  # noqa: F401
from app.models import category as _category  # noqa: F401
from app.models import coupon as _coupon  # noqa: F401
from app.models import coupon_verification as _coupon_verification  # noqa: F401
from app.models import ingestion_run as _ingestion_run  # noqa: F401
from app.models import page as _page  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models import store as _store  # noqa: F401
from app.models import user as _user  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
