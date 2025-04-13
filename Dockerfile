FROM python:3.11-slim

# Installer Tesseract et les dépendances système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    ttf-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Définir le dossier de travail
WORKDIR /app

# Copier les dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier tout le reste de l'app
COPY . .

# Lancer l'application
CMD ["python", "main.py"]
