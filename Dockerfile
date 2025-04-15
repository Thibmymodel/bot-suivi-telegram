# Dockerfile
FROM python:3.11-slim

# Empêche les prompts interactifs
ENV DEBIAN_FRONTEND=noninteractive

# Installation des dépendances système et de Tesseract
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copie et installation des dépendances Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code
COPY . .

# Port par défaut pour Render (utile même si FastAPI utilise webhook)
ENV PORT 10000
EXPOSE 10000

# Commande de lancement
CMD ["python", "main.py"]
