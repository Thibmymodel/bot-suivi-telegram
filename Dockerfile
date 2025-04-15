FROM python:3.11-slim

# Installation des dépendances système
RUN apt-get update && \
    apt-get install -y tesseract-ocr libtesseract-dev libleptonica-dev poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR /app

# Copie des fichiers de l'application
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Port par défaut pour Render
EXPOSE 10000

# Commande pour lancer FastAPI avec Uvicorn
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
