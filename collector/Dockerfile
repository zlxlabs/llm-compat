FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

EXPOSE 8000

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

CMD ["uvicorn", "collector.app:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
