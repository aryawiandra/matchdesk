FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
RUN pip install --no-cache-dir "agent-framework-core>=1.22.0" "agent-framework-ollama>=1.0.0b261015" \
    "fastapi>=0.147.0" "pydantic>=2.7" "uvicorn>=0.54.0"
COPY src ./src
COPY web ./web
COPY data/recorded ./data/recorded
USER nobody
EXPOSE 8000
# No MATCHDESK_MODEL: the public instance replays recorded model answers through the live Verifier.
CMD ["uvicorn", "--factory", "matchdesk.feed:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
