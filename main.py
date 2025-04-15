import os
import logging
import shutil
import pytesseract
import subprocess
from PIL import Image
import io
from fastapi import FastAPI
from telegram.ext import Application, CommandHandler

# Configuration du logging
logging.basicConfig(level=logging.INFO)

# Ajoute les chemins manuellement au PATH pour garantir que Tesseract est détectable
os.environ["PATH"] = "/usr/bin:/usr/local/bin:" + os.environ.get("PATH", "")

# Vérifie et configure Tesseract avec plusieurs chemins possibles
POTENTIAL_PATHS = [
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/app/.apt/usr/bin/tesseract"
]

# Log PATH et contenu des répertoires pour debug Render
try:
    logging.info(f"🔍 PATH actuel : {os.environ.get('PATH')}")
    logging.info("📁 Contenu de /usr/bin :")
    result = subprocess.run(["ls", "-la", "/usr/bin"], capture_output=True, text=True)
    logging.info(result.stdout)
except Exception as e:
    logging.warning(f"Erreur lors de l'inspection du système : {e}")

# Test direct : tesseract -v
try:
    version_check = subprocess.run(["tesseract", "-v"], capture_output=True, text=True)
    logging.info("📦 tesseract -v :")
    logging.info(version_check.stdout or version_check.stderr)
except Exception as e:
    logging.warning(f"❌ Erreur lors de l'exécution de tesseract -v : {e}")

# Recherche du binaire tesseract dans le PATH
which_result = shutil.which("tesseract")
logging.info(f"🔍 Résultat de shutil.which('tesseract') : {which_result}")

TESSERACT_PATH = which_result or next((p for p in POTENTIAL_PATHS if os.path.exists(p)), None)

if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
    logging.info(f"✅ Tesseract trouvé à : {TESSERACT_PATH}")
    logging.info(f"🔧 pytesseract utilisera ce chemin : {pytesseract.pytesseract.tesseract_cmd}")
    try:
        version = pytesseract.get_tesseract_version()
        logging.info(f"📦 Version Tesseract (via pytesseract) : {version}")

        # Test OCR minimaliste (image blanche vide)
        test_img = Image.new("RGB", (100, 30), color=(255, 255, 255))
        buf = io.BytesIO()
        test_img.save(buf, format='PNG')
        buf.seek(0)
        pytesseract.image_to_string(Image.open(buf))
        logging.info("🔍 Test OCR exécuté avec succès ✅")

    except Exception as e:
        logging.warning(f"⚠️ Impossible d'obtenir la version ou d'exécuter un test OCR : {e}")
else:
    logging.error("❌ Aucun chemin Tesseract trouvé. Valeur shutil.which : %s", which_result)
    logging.error("🔴 OCR désactivé – vérifie que Tesseract est bien installé et dans le PATH.")

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
