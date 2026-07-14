FROM python:3.13-slim

# Xvfb: virtual framebuffer for headless=False Firefox.
# ca-certificates: needed by Playwright's browser downloader.
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Layer 1: Python deps ────────────────────────────────────────────────────
# Install uv, then use the lockfile to get reproducible versions.
# A stub src/__init__.py lets pip resolve the editable install without the
# real source; the actual code is copied in a later (cheaper) layer.
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN mkdir -p src && touch src/__init__.py \
 && uv pip install --system --no-cache -e .

# ── Layer 2: Playwright Firefox ─────────────────────────────────────────────
# This downloads ~300 MB and installs system libs (libgtk, libdbus, etc.).
# Kept as its own layer so a source change does not re-download the browser.
RUN playwright install firefox \
 && playwright install-deps firefox

# ── Layer 3: Application source ─────────────────────────────────────────────
COPY src/ ./src/
COPY debug_contact.py debug_immoscout.py ./

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
# Default to Immowelt; overridden per-service in docker-compose.yml.
CMD ["immowelt"]
