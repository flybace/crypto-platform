FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY contracts ./contracts

RUN python -m pip install --no-cache-dir ".[runtime]"

EXPOSE 8000

USER 65532:65532

CMD ["uvicorn", "web.http_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
