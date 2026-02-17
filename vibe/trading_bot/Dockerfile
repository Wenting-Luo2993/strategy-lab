# Multi-stage build for optimized production image

# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY vibe/trading_bot/requirements.txt .

# Install dependencies to /build/venv
RUN python -m venv /build/venv && \
    /build/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Install runtime dependencies (curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 trading

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /build/venv /app/venv

# Copy application code (entire vibe package for proper imports)
COPY --chown=trading:trading vibe/ /app/vibe/

# Create data and logs directories (will be mounted as volumes)
RUN mkdir -p /app/data /app/logs && chown trading:trading /app/data /app/logs

# Set environment variables
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app" \
    ENVIRONMENT=production \
    LOG_LEVEL=INFO

# Switch to non-root user
USER trading

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Use exec form for proper signal handling (PID 1)
ENTRYPOINT ["/app/venv/bin/python", "-m", "vibe.trading_bot.main"]
CMD ["run"]
