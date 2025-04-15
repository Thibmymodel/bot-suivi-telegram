FROM python:3.11-slim

# Tesseract + dépendances
RUN apt-get update && \
    apt-get install -y tesseract-ocr libglib2.0-0 libsm6 libxrender1 libxext6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8000
EXPOSE 8000

# 🔍 CMD conditionnel en fonction de la variable DEBUG_SYSTEM_INFO
CMD if [ "$DEBUG_SYSTEM_INFO" = "true" ]; then \
        echo "🔎 DEBUG ACTIVÉ – PATH = $PATH" && \
        echo "📂 Contenu /usr/bin (grep tesseract):" && \
        ls -l /usr/bin | grep tesseract && \
        echo "📌 which tesseract:" && which tesseract && \
        echo "📌 Version :" && tesseract --version; \
    fi && \
    python main.py
