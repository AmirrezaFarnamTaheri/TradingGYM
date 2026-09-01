# Use the official Python 3.12 slim image as a parent image
FROM python:3.12-slim

# Set the working directory in the container to /app
WORKDIR /app

# Copy the dependency files to the working directory
COPY pyproject.toml poetry.lock* ./

# Install poetry (pinned; the lock file drives every version below)
RUN pip install --no-cache-dir poetry==1.8.5 \
    && poetry config virtualenvs.create false

# Install ONLY the runtime dependencies (--only main replaces the
# removed-in-Poetry-1.2 --no-dev flag; the lock file is respected)
RUN poetry install --only main --no-root

# Copy the rest of the application's source code from your host to your image filesystem.
COPY . .

# Install the project in editable mode
RUN poetry install --only main

# Run as an unprivileged user (was root)
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /home/appuser/.trading-gym \
    && chown -R appuser:appuser /app /home/appuser/.trading-gym
USER appuser

ENV TRADING_GYM_HOME=/home/appuser/.trading-gym

# Container liveness: the API serves /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status == 200 else 1)" || exit 1

# Set the default command to run when the container starts
CMD ["/bin/bash"]
