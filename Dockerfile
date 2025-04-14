FROM python:3.11

# Installer tesseract et ses dépendances système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Répertoire de travail
WORKDIR /opt/render/project/src

# Installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code
COPY . .

# Commande de lancement
CMD ["uvicorn", "main:app_fastapi", "--host", "0.0.0.0", "--port", "10000"]
