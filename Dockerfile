# ═══════════════════════════════════════════════════════════════════
# SGOS Backend — Docker Image
# Multi-stage: deps → app (slim final image)
# ═══════════════════════════════════════════════════════════════════

FROM python:3.11-slim AS base

# System deps for whisper, ffmpeg, and data processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for fast dep management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install Python deps first (cache layer)
COPY pyproject.toml requirements*.txt ./
RUN if [ -f requirements.txt ]; then uv pip install --system -r requirements.txt; fi

# Copy app code
COPY . .

# Create data directories
RUN mkdir -p /app/data

# Expose port
EXPOSE 8420

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8420/health || exit 1

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8420", "--log-level", "info"]
