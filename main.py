import os
import io
import re
import pytesseract
import shutil
import logging
import datetime
import httpx
import subprocess
from PIL import Image
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.ext import Defaults, CallbackContext

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔍 (NOUVEAU) Informations système pour le debug Render
def log_system_environment():
    logger.info(f"📁 PATH système : {os.getenv('PATH')}")
    logger.info("🔍 Contenu des chemins standards :")
    for path in ["/usr/bin", "/usr/local/bin", "/bin"]:
        try:
            if os.path.exists(path):
                logger.info(f"📂 {path} : {os.listdir(path)}")
        except Exception as e:
            logger.warning(f"⚠️ Erreur en listant {path} : {e}")

log_system_environment()

# 🔍 Détection robuste de Tesseract
def detect_tesseract_path():
    candidates = [
        shutil.which("tesseract"),
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/bin/tesseract"
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            logger.info(f"✅ Tesseract détecté à : {path}")
            return path
    logger.critical("❌❌ Aucun binaire Tesseract trouvé. OCR désactivé.")
    return None

tesseract_path = detect_tesseract_path()
pytesseract.pytesseract.tesseract_cmd = tesseract_path
logger.info(f"📌 pytesseract utilisera : {pytesseract.pytesseract.tesseract_cmd}")

# 🧪 Test de bon fonctionnement de Tesseract
if tesseract_path:
    try:
        result = subprocess.run([tesseract_path, "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"🧪 Tesseract fonctionne : {result.stdout.splitlines()[0]}")
        else:
            logger.error(f"❌ Tesseract erreur d'exécution : {result.stderr}")
    except Exception as e:
        logger.exception("❌ Exception lors du test de Tesseract")

# Configuration du bot Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_BOT_TOKEN non défini dans les variables d'environnement.")
BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Création de l'application FastAPI et Telegram
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Initialisation FastAPI
app_fastapi = FastAPI()

@app_fastapi.on_event("startup")
async def on_startup():
    try:
        async with httpx.AsyncClient() as client:
            url = f"{BASE_URL}/setWebhook"
            webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://bot-suivi-telegram.onrender.com") + "/webhook"
            response = await client.post(url, json={"url": webhook_url})
            if response.status_code == 200:
                logger.info("✅ Webhook Telegram configuré.")
            else:
                logger.warning(f"⚠️ Webhook non configuré. Code HTTP: {response.status_code}")
    except Exception as e:
        logger.exception("Erreur lors de la configuration du webhook")

@app_fastapi.on_event("shutdown")
async def on_shutdown():
    logger.info("🛑 Arrêt du bot Telegram.")

# ----------- Fonctions principales -----------

def extract_text_from_image(image_bytes: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(image)
    except Exception as e:
        logger.exception("❌ Erreur lors de l'extraction OCR")
        return ""

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.photo:
            return

        file = await context.bot.get_file(update.message.photo[-1].file_id)
        image_bytes = await file.download_as_bytearray()
        text = extract_text_from_image(image_bytes)

        if text.strip():
            await update.message.reply_text(f"🧾 Texte extrait :\n{text[:1000]}")
        else:
            await update.message.reply_text("❌ Aucun texte détecté dans l'image.")
    except Exception as e:
        logger.exception("Erreur lors du traitement de l'image")
        await update.message.reply_text("❌ Une erreur est survenue lors de l'analyse de l'image.")

# ----------- Webhook FastAPI -----------

@app_fastapi.post("/webhook")
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return JSONResponse(content={"status": "ok"}, status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.exception("Erreur webhook")
        return JSONResponse(content={"status": "error", "detail": str(e)}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ----------- Handlers Telegram -----------

application.add_handler(MessageHandler(filters.PHOTO, handle_image))

logger.info("✅ Serveur FastAPI prêt.")
