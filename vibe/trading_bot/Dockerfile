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
COPY requirements.txt .

# Install dependencies to /build/venv
RUN python -m venv /build/venv && \
    /build/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Create non-root user
RUN useradd -m -u 1000 trading

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /build/venv /app/venv

# Copy application code
COPY --chown=trading:trading . .

# Set environment variables
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    LOG_LEVEL=INFO

# Switch to non-root user
USER trading

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Use exec form for proper signal handling (PID 1)
ENTRYPOINT ["python", "-m", "vibe.trading_bot.main"]
CMD ["run"]
