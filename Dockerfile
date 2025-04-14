FROM python:3.11-slim

# Installer Tesseract + dépendances
RUN apt-get update && \
    apt-get install -y tesseract-ocr libtesseract-dev libleptonica-dev poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Forcer l’ajout au PATH
ENV TESSERACT_PATH="/usr/bin/tesseract"
ENV PATH="$PATH:/usr/bin"

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code
COPY . /app
WORKDIR /app

# Exécuter le bot
CMD ["python", "main.py"]

ENV PATH="/usr/bin:$PATH"
