import os
import logging
import shutil
import pytesseract
from fastapi import FastAPI
from telegram.ext import Application, CommandHandler

# Vérifie et configure Tesseract
TESSERACT_PATH = shutil.which("tesseract")
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    logging.info(f"✅ Tesseract trouvé à : {TESSERACT_PATH}")
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
    logging.basicConfig(level=logging.INFO)
    logging.info("✅ Démarrage du bot Telegram...")

    # Lance le webhook (via Render)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL,
    )
