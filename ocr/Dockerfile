FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Pin max version installable on linux + cp313 from PyPI wheels (newer majors may lag per ABI).
RUN pip install --no-cache-dir \
    "paddlepaddle==3.2.2" \
    "paddleocr>=2.7" \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.29" \
    "httpx>=0.27" \
    "numpy>=1.26" \
    "opencv-python-headless>=4.9"

# Full OCR package (service imports ``paddle_extract``, etc.).
COPY ocr/*.py /app/ocr/
COPY layout/ /app/layout/

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "ocr.service:app", "--host", "0.0.0.0", "--port", "8000"]
