import os
import json
import pytesseract
from PIL import Image
import logging
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    CommandHandler,
    filters,
    Application
)
from fastapi import FastAPI, Request
import uvicorn

# Configuration FastAPI pour le webhook
app_fastapi = FastAPI()

# Configuration manuelle des tokens (à défaut des variables Render)
BOT_TOKEN = "7627601916:AAHoCOA3MxpHQxjSz4WA2eIvWJrby6ty0d4"
GROUP_ID = -1002317321058
REPLY_DELAY = 10  # minutes

# Chargement des identifiants Google depuis Render (clé JSON sous forme de string)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
try:
    credentials_dict = json.loads(credentials_json)
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
except Exception as e:
    print(f"Erreur lors du chargement des identifiants Google : {e}")
    raise

# Accès Google Sheet
gc = gspread.authorize(credentials)
sheet = gc.open_by_key("1__RzRpZKj0kg8Cl0QB-D91-hGKKff9SqsOQRE0GvReE")
worksheet = sheet.worksheet("Données Journalières")

# Stockage des images temporaires
pending_images = {}

# OCR utilitaire
def extract_info_from_image(image_path):
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)

        network = "Instagram" if "instagram" in text.lower() else (
            "Twitter" if "twitter" in text.lower() else (
            "Threads" if "threads" in text.lower() else (
            "TikTok" if "tiktok" in text.lower() else "Inconnu")))

        lines = text.split("\n")
        account = "inconnu"
        followers = -1
        for line in lines:
            if "@" in line and account == "inconnu":
                account = line.strip()
            if "abonnés" in line.lower() or "followers" in line.lower():
                digits = ''.join([c if c.isdigit() or c in "kKmM.," else '' for c in line])
                digits = digits.replace(',', '.')
                if 'k' in digits.lower():
                    followers = int(float(digits.lower().replace('k','')) * 1000)
                elif 'm' in digits.lower():
                    followers = int(float(digits.lower().replace('m','')) * 1000000)
                elif digits:
                    followers = int(float(digits))
        return network, account, followers
    except Exception as e:
        print(f"Erreur OCR: {e}")
        return "Inconnu", "inconnu", -1

# Tâche de fond pour traiter les images
async def handle_pending(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for user_id in list(pending_images.keys()):
        images = pending_images[user_id]
        if (now - images["timestamp"]).total_seconds() > REPLY_DELAY * 60:
            results = []
            for file_path in images["files"]:
                res = extract_info_from_image(file_path)
                results.append(res)
                try:
                    worksheet.append_row([
                        datetime.now().strftime("%Y-%m-%d"),
                        res[0], res[1], res[2],
                        user_id
                    ])
                except Exception as e:
                    print(f"Erreur ajout Google Sheet: {e}")
            msg = f"🤖 {datetime.now().strftime('%d/%m')} – {len(results)} comptes détectés et ajoutés ✅"
            await context.bot.send_message(chat_id=user_id, text=msg)
            del pending_images[user_id]

# Gestion des images
async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != GROUP_ID:
        return

    if not update.message.photo:
        return

    user_id = update.message.chat_id
    file = await context.bot.get_file(update.message.photo[-1].file_id)
    file_path = f"temp_{update.message.message_id}.jpg"
    await file.download_to_drive(file_path)

    if user_id not in pending_images:
        pending_images[user_id] = {"files": [], "timestamp": datetime.now()}
    pending_images[user_id]["files"].append(file_path)

# Initialisation de l'application Telegram
bot_app = Application.builder().token(BOT_TOKEN).build()
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))
bot_app.job_queue.run_repeating(handle_pending, interval=60)

# Lancement asynchrone de bot_app
import asyncio
async def run_bot():
    await bot_app.initialize()
    await bot_app.start()
    print("🤖 Bot Telegram prêt à recevoir les mises à jour via webhook")

asyncio.get_event_loop().create_task(run_bot())

# Endpoint webhook (appelé par Telegram)
@app_fastapi.post("/webhook")
async def telegram_webhook(req: Request):
    body = await req.json()
    update = Update.de_json(body, bot_app.bot)
    await bot_app.process_update(update)
    return {"status": "ok"}

# Lancement local uniquement pour test
if __name__ == "__main__":
    print("🚀 Lancement local du serveur webhook sur http://localhost:8000")
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8000, reload=True)
