# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL build_date="2026-02-26" version="1.2"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Install only supervisor (no build tools needed at runtime)
RUN apt-get update && apt-get install -y \
    supervisor \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose port for the UI Server
EXPOSE 8000

# Ensure installed packages are on PATH
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Command to run supervisor (starts both agent and UI)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
