FROM python:3.11-slim

WORKDIR /opt/render/project/src

# Installation de Tesseract et dépendances utiles à l'OCR
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libtesseract-dev \
        libleptonica-dev \
        pkg-config \
        poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Ajout explicite de /usr/local/bin au PATH (par précaution pour Tesseract)
ENV PATH="/usr/local/bin:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
