FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "paddlepaddle>=3.0" \
    "paddleocr>=2.7" \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.29" \
    "httpx>=0.27" \
    "numpy>=1.26" \
    "opencv-python-headless>=4.9"

COPY ocr/service.py /app/ocr/service.py
COPY layout/ /app/layout/

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "ocr.service:app", "--host", "0.0.0.0", "--port", "8000"]
