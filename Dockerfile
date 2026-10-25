FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ARG SOURCE_REVISION=unrecorded
ENV TRADING_GYM_BUILD_REVISION=$SOURCE_REVISION
WORKDIR /app
COPY pyproject.toml poetry.lock README.md ./
RUN pip install --no-cache-dir poetry==2.2.1 && POETRY_VIRTUALENVS_CREATE=false poetry install --only main --no-root
COPY trading_gym/ ./trading_gym/
COPY config/ ./config/
RUN POETRY_VIRTUALENVS_CREATE=false poetry install --only main
COPY --from=frontend /ui/dist ./frontend/dist
RUN useradd --create-home --shell /usr/sbin/nologin appuser && mkdir -p /home/appuser/.trading-gym && chown -R appuser:appuser /app /home/appuser/.trading-gym
USER appuser
ENV TRADING_GYM_HOME=/home/appuser/.trading-gym
VOLUME ["/home/appuser/.trading-gym", "/app/config"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"
CMD ["python", "-m", "trading_gym.cli", "serve"]
