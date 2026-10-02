"""Shared rate limiter (slowapi / the `limits` library underneath).

In-memory storage by default — fine for a single instance. If you run more
than one API process behind a load balancer, point this at Redis instead
(already provisioned in docker-compose) so limits are shared across
instances: Limiter(key_func=client_ip, storage_uri=settings.redis_url).
"""

from slowapi import Limiter

from app.core.request_meta import client_ip

limiter = Limiter(key_func=client_ip)
