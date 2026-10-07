FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

WORKDIR /srv
# Compile the packages to bytecode at build time, so a cold start doesn't do it on the first request.
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY demo_data ./demo_data
COPY registry.yaml policies.yaml ./
RUN /srv/.venv/bin/python -m compileall -q app

# Start uvicorn straight from the environment: `uv run` checks the environment first, on every start.
ENV PORT=8080 PATH="/srv/.venv/bin:$PATH"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
