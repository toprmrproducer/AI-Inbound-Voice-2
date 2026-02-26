# Use official Python runtime as a parent image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Cache-bust label — update this whenever Coolify skips the build
LABEL build_date="2026-02-26" version="1.1"

# Install dependencies (adding supervisor)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ca-certificates \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose port for the UI Server
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Command to run supervisor (starts both agent and UI)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
