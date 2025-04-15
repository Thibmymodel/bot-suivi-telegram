# Utilise une image Python légère
FROM python:3.11-slim

# 👇 Installe Tesseract OCR et ses dépendances
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libtesseract-dev \
        libleptonica-dev \
        pkg-config \
        poppler-utils \
        curl \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Installe les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie le reste du code dans le conteneur
COPY . /app
WORKDIR /app

# Lance l'application
CMD ["python", "main.py"]
