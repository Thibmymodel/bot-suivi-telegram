FROM python:3.11-slim

# Install Tesseract OCR
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Expose port for FastAPI
ENV PORT=10000
EXPOSE 10000

# Launch command
CMD ["python", "main.py"]
