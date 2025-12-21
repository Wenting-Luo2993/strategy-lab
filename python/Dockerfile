# Multi-stage Dockerfile for Strategy Lab
# Optimized for Oracle Cloud Always Free service
# Uses Python 3.12 for pandas_ta compatibility
# Located in /python directory

# Stage 1: Builder
FROM python:3.12-slim as builder

WORKDIR /app

# Install system dependencies required for Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    python3-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY python/requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Install minimal runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application code
COPY python/ /app/python/

WORKDIR /app/python

# Create necessary directories for logs and data cache
RUN mkdir -p logs data_cache results/backtest

# Health check (optional)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1 || true

# Default command - runs the market hours orchestrator
# This keeps the container running 24/7 and starts scripts during market hours
CMD ["python", "orchestrator_main.py"]

# Alternative commands:
# - For backtesting only: python main.py
# - For testing orchestrator: python scripts/test_finnhub_orchestrator.py
# - For custom script: python <script_name>.py
