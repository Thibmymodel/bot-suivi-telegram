# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install system dependencies including tesseract
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /opt/render/project/src

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy full app code
COPY . .

# Launch with uvicorn (FastAPI)
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
