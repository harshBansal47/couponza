FROM python:3.12-slim

WORKDIR /code

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# Run as a non-root user: if the app is ever compromised (a dependency CVE, an
# injection bug we missed), this limits what the attacker's process can touch.
# UID 1000 matches the typical first non-root user on Linux hosts, so writes
# into the bind-mounted ./app and ./alembic (docker-compose.yml) don't hit
# permission errors in local dev on native Linux.
RUN useradd --create-home --uid 1000 --shell /bin/false appuser \
    && chown -R appuser:appuser /code
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
