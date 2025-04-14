FROM python:3.11-slim

# Installation de Tesseract OCR + dépendances système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Création du dossier d'app
WORKDIR /app

# Copie du code
COPY . /app

# Installation des dépendances Python
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Exposition du port attendu par Render
EXPOSE 10000

# Lancement automatique
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
