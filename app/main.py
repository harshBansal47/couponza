from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.admin import setup_admin
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine
from app.core.limiter import limiter
from app.core.security_headers import SecurityHeadersMiddleware
from app.routers.api.v1.account import router as account_router
from app.routers.api.v1.ads import router as ads_router
from app.routers.api.v1.auth import router as auth_router
from app.routers.api.v1.categories import router as categories_router
from app.routers.api.v1.coupons import router as coupons_router
from app.routers.api.v1.pages import router as pages_router
from app.routers.api.v1.products import router as products_router
from app.routers.api.v1.stores import router as stores_router

settings = get_settings()

app = FastAPI(title="Couponza", version="0.1.0")

# Rate limiting (auth endpoints, coupon verification — see each router).
app.state.limiter = limiter
# slowapi's handler is typed for RateLimitExceeded specifically; Starlette's
# add_exception_handler wants the broader Exception signature. Harmless at
# runtime (RateLimitExceeded IS an Exception) — a known typing variance, not
# a real bug.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_middleware(SlowAPIMiddleware)

# Security headers on every response.
app.add_middleware(SecurityHeadersMiddleware)

# CORS: without this, a browser blocks every client-side fetch the Next.js
# frontend makes to this API (server-side fetches in Next.js Server Components
# aren't affected — only the browser enforces CORS). Configure via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in (
    auth_router,
    categories_router,
    stores_router,
    coupons_router,
    ads_router,
    pages_router,
    products_router,
    account_router,
):
    app.include_router(_router, prefix="/api/v1")


# Back office at /admin (admins and editors only).
setup_admin(
    app,
    engine,
    AsyncSessionLocal,
    secret_key=settings.secret_key,
    https_only=settings.environment == "production",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Basic liveness check — confirms the app booted and can read its settings."""
    return {"status": "ok", "environment": settings.environment}
