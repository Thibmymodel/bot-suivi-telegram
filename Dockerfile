FROM python:3.11-slim

# Installer Tesseract et dépendances
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    && apt-get clean

# Copier les fichiers du projet
WORKDIR /app
COPY . /app

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Lancer le bot
CMD ["python", "main.py"]
