FROM python:3.11-slim

# -------------------
# INSTALL TESSERACT
# -------------------
RUN apt-get update && \
    apt-get install -y tesseract-ocr libtesseract-dev libleptonica-dev \
    && rm -rf /var/lib/apt/lists/*

# -------------------
# INSTALL DEPENDENCIES
# -------------------
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# -------------------
# COPY SOURCE
# -------------------
COPY . .

# -------------------
# PORT POUR RENDER
# -------------------
EXPOSE 10000

# -------------------
# LAUNCH
# -------------------
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
