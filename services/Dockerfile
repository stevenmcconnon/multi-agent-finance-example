# Build context is the repo root (compose: build.context "."), so db/migrations ships
# alongside the package: finance_agents.db resolves MIGRATIONS_DIR as <repo>/db/migrations.
FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.5.14
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first so a code change does not re-resolve the lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/ services/
COPY db/ db/
RUN uv sync --frozen --no-dev

# In production: the fastembed ONNX model is baked into the image (or mounted from EFS)
#   so a task is ready at start.
# In this demo: the model downloads lazily on first embed into FASTEMBED_CACHE_PATH, because
#   baking ~130 MB into every build slows the sample and the compose volume caches it anyway.
RUN useradd --create-home --uid 1000 app && mkdir -p /home/app/.cache/fastembed && chown -R app:app /app /home/app
USER app
ENV FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "finance_agents.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
