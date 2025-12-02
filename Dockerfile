# Use a minimal Python base image
FROM python:3.12-alpine

# Build arguments for CI/CD
ARG BUILD_DATE
ARG VCS_REF
ARG VERSION=latest

# Set OCI labels for container metadata
LABEL maintainer="Docker Image Watch" \
      description="Automatic Docker container update checker and updater" \
      org.opencontainers.image.title="Docker Image Watch" \
      org.opencontainers.image.description="Monitors running containers for image updates and applies them automatically" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT"

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Health check - verify Python process is running
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD pgrep -f "python.*main.py" > /dev/null || exit 1

# Run the application
CMD ["python", "-u", "app/main.py"]
