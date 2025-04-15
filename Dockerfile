# Dockerfile complet avec installation de Tesseract OCR

FROM python:3.11

# 📦 Installation de Tesseract OCR et dépendances système
RUN apt-get update && \
    apt-get install -y tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

# 📁 Création du répertoire de travail
WORKDIR /app

# 🔄 Copie des fichiers nécessaires
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 🚀 Lancement du bot
CMD ["python", "main.py"]