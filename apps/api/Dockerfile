# FastAPI dev image
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir uv pipx && pipx install poetry || true
# Install deps
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings redis
COPY app /app/app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
