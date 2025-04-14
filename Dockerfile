FROM python:3.11-slim

# Préinstallation des dépendances système nécessaires
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    libgl1-mesa-glx \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de l'application
COPY . .

# Installer les dépendances Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Confirmer la présence de Tesseract
RUN which tesseract || echo "Tesseract non trouvé dans le PATH"

# Exposer le port attendu
EXPOSE 10000

# Lancer le bot via FastAPI
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
