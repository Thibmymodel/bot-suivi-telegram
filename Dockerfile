FROM python:3.11-slim

# Dépendances système
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    tesseract-ocr-fra \
    poppler-utils \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Logs de vérification de Tesseract
RUN echo "🧪 Test binaire Tesseract" && \
    which tesseract && \
    tesseract --version

# Dépendances Python
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copie du code
COPY . .

# Lance le bot
CMD ["python", "main.py"]
