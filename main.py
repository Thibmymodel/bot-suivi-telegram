import os
import logging
import shutil
import pytesseract
import subprocess
from fastapi import FastAPI
from telegram.ext import Application, CommandHandler

# Configuration du logging
logging.basicConfig(level=logging.INFO)

# Vérifie et configure Tesseract avec plusieurs chemins possibles
POTENTIAL_PATHS = [
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/app/.apt/usr/bin/tesseract"
]

# Log PATH et contenu du répertoire /usr/bin pour debug Render
try:
    logging.info(f"🔍 PATH actuel : {os.environ.get('PATH')}")
    logging.info("📁 Contenu de /usr/bin :")
    result = subprocess.run(["ls", "-la", "/usr/bin"], capture_output=True, text=True)
    logging.info(result.stdout)
except Exception as e:
    logging.warning(f"Erreur lors de l'inspection du système : {e}")

TESSERACT_PATH = shutil.which("tesseract") or next((p for p in POTENTIAL_PATHS if os.path.exists(p)), None)

if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    logging.info(f"✅ Tesseract trouvé à : {TESSERACT_PATH}")
    try:
        version = pytesseract.get_tesseract_version()
        logging.info(f"📦 Version Tesseract : {version}")
    except Exception as e:
        logging.warning(f"⚠️ Impossible d'obtenir la version de Tesseract : {e}")
else:
    logging.error("❌ Tesseract non détecté. OCR désactivé.")

# Initialise FastAPI
app = FastAPI()

# Initialise Telegram bot
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))

application = Application.builder().token(BOT_TOKEN).build()

# Commande /start
async def start(update, context):
    await update.message.reply_text("Bot opérationnel ✅")

application.add_handler(CommandHandler("start", start))

# Lancement FastAPI et Telegram Webhook
if __name__ == "__main__":
    logging.info("✅ Démarrage du bot Telegram...")

    # Lance le webhook (via Render)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )