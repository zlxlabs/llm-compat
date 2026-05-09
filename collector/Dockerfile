FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/

EXPOSE 8000

CMD ["uvicorn", "collector.app:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
