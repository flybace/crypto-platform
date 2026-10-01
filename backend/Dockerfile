FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY contracts ./contracts
COPY backend ./backend
COPY scripts ./scripts

RUN python -m pip install --no-cache-dir ".[runtime]"

EXPOSE 8000

USER 65532:65532

CMD ["uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
