FROM ghcr.io/astral-sh/uv:python3.11-bookworm
WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY src ./src
RUN uv sync --frozen --no-cache
COPY . .
ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
