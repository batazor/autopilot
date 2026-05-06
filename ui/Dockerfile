# Lightweight dashboard only (no pyobjc / emulator deps). Mount `./config` with Redis URL pointing at your broker (e.g. `redis://redis:6379/0` in Compose).
FROM python:3.13-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir \
    "streamlit>=1.39" \
    "streamlit-autorefresh>=1.0" \
    "redis>=5.0" \
    "pyyaml>=6.0"

COPY config /app/config
COPY ui /app/ui

ENV PYTHONPATH=/app

EXPOSE 8501

CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
