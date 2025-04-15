import os
import logging
import shutil
import pytesseract
from fastapi import FastAPI

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

# ------------- Logging -------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------- FastAPI -------------
app_fastapi = FastAPI()

@app_fastapi.get("/")
async def root():
    return {"status": "Bot opérationnel ✅"}

# ------------- Détection Tesseract -------------
def detect_tesseract_path():
    possible_paths = [
        shutil.which("tesseract"),
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/bin/tesseract"
    ]
    for path in possible_paths:
        if path and os.path.exists(path):
            logger.info(f"✅ Tesseract détecté : {path}")
            return path
    logger.error("❌ Tesseract non détecté. OCR désactivé.")
    return None

# ------------- Configuration OCR -------------
tesseract_path = detect_tesseract_path()
if tesseract_path:
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
    try:
        version = pytesseract.get_tesseract_version()
        logger.info(f"🔍 Version de Tesseract : {version}")
    except Exception as e:
        logger.warning(f"⚠️ Impossible de lire la version de Tesseract : {e}")

# ------------- Fonction de traitement -------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("📩 Message reçu")
    await update.message.reply_text("Bot actif ✅")

# ------------- Initialisation Telegram -------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))

if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN non défini")
else:
    try:
        from telegram.ext import Application
        application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        application.add_handler(MessageHandler(filters.ALL, handle_message))

        logger.info("✅ Démarrage du bot Telegram...")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL
        )
    except RuntimeError as e:
        logger.error(f"❌ Erreur lors de l'initialisation du webhook : {e}")
        logger.error("💡 Essayez : pip install 'python-telegram-bot[webhooks]'")