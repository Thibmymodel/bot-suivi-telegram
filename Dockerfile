FROM python:3.11-slim

# Installation de Tesseract et dépendances système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Création du dossier de l'app
WORKDIR /app

# Copie des fichiers
COPY . /app

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Définir le path de Tesseract pour pytesseract
ENV TESSDATA_PREFIX=/usr/share/tesser_
