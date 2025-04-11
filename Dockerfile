FROM python:3.11-slim

# Installer tesseract + dépendances
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
 && rm -rf /var/lib/apt/lists/*

# Copier les fichiers
WORKDIR /app
COPY . /app

# Installer les dépendances
RUN pip install --upgrade pip && pip install -r requirements.txt

# Lancer le bot
CMD ["python", "main.py"]
