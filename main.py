import os
import json
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
import logging
import gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    CommandHandler,
    filters,
)
from fastapi import FastAPI, Request
import uvicorn

app_fastapi = FastAPI()

# Config depuis Render (sécurisée)
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROUP_ID = int(os.environ["TELEGRAM_GROUP_ID"])
REPLY_DELAY = 5

# Auth Google
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials_dict = json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
credentials = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)

gc = gspread.authorize(credentials)
sheet = gc.open_by_key(os.environ["SPREADSHEET_ID"])
worksheet = sheet.worksheet("Données Journalières")

pending_images = {}

def try_ocr_variants(image_path):
    try:
        img = Image.open(image_path)

        variants = [
            img,
            ImageOps.grayscale(img),
            ImageEnhance.Contrast(ImageOps.grayscale(img)).enhance(2),
            ImageOps.invert(ImageOps.grayscale(img)),
            img.resize((img.size[0]*2, img.size[1]*2))
        ]

        for variant in variants:
            text = pytesseract.image_to_string(variant, lang="eng+fra")
            if any(x in text.lower() for x in ["followers", "abonnés", "abonnements", "suivis", "publications", "suivi(e)s"]):
                return text

        return None
    except Exception as e:
        print(f"Erreur OCR variante : {e}")
        return None

def extract_info_from_image(image_path):
    try:
        text = try_ocr_variants(image_path)
        if not text:
            return "Inconnu", "ECHEC OCR ❌", -1

        lines = text.split("\n")
        text_lower = text.lower()

        if "threads" in text_lower:
            network = "Threads"
        elif "tiktok" in text_lower or "j'aime" in text_lower:
            network = "TikTok"
        elif "twitter" in text_lower or "tweets" in text_lower or "abonnés" in text_lower and "rejoint x" in text_lower:
            network = "Twitter"
        elif "followers" in text_lower or "publications" in text_lower or "suivi(e)s" in text_lower:
            network = "Instagram"
        else:
            network = "Inconnu"

        account = "inconnu"
        followers = -1

        for line in lines:
            if "@" in line and account == "inconnu":
                account = line.strip().split()[0]
            if any(x in line.lower() for x in ["followers", "abonnés"]):
                digits = ''.join([c if c.isdigit() or c in "kKmM.," else '' for c in line])
                digits = digits.replace(',', '.')
                if 'k' in digits.lower():
                    followers = int(float(digits.lower().replace('k','')) * 1000)
                elif 'm' in digits.lower():
                    followers = int(float(digits.lower().replace('m','')) * 1000000)
                elif digits:
                    followers = int(float(digits))

        if account == "inconnu" or followers == -1:
            account = "ECHEC OCR ❌"

        return network, account, followers
    except Exception as e:
        print(f"Erreur OCR: {e}")
        return "Inconnu", "ECHEC OCR ❌", -1

def get_previous_count(account_name):
    try:
        records = worksheet.get_all_records()
        for row in reversed(records):
            if row['Compte'] == account_name and row['Abonnés'] > 0:
                return row['Abonnés']
    except Exception as e:
        print(f"Erreur lecture ancienne valeur: {e}")
    return 0

async def handle_pending(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for user_id in list(pending_images.keys()):
        images = pending_images[user_id]
        if (now - images["timestamp"]).total_seconds() > REPLY_DELAY * 60:
            results = []
            for file_path in images["files"]:
                res = extract_info_from_image(file_path)

                today = datetime.now().strftime("%Y-%m-%d")
                all_rows = worksheet.get_all_records()
                if any(r['Date'] == today and r['Compte'] == res[1] for r in all_rows):
                    continue

                previous = get_previous_count(res[1])
                evolution = res[2] - previous if res[2] > 0 else 0

                try:
                    worksheet.append_row([
                        today,
                        context.bot.get_chat(user_id).username if context.bot.get_chat(user_id).username else "@inconnu",
                        res[0],
                        res[1],
                        res[2],
                        evolution
                    ])
                    results.append(res)
                except Exception as e:
                    print(f"Erreur ajout Google Sheet: {e}")
            msg = f"🤖 {datetime.now().strftime('%d/%m')} – {len(results)} comptes détectés et ajoutés ✅"
            await context.bot.send_message(chat_id=user_id, text=msg)
            del pending_images[user_id]

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat_id != GROUP_ID:
        return
    if not update.message.photo:
        return

    user_id = update.message.chat_id
    file = await context.bot.get_file(update.message.photo[-1].file_id)
    file_path = f"temp_{update.message.message_id}.jpg"
    await file.download_to_drive(file_path)
    print(f"Image téléchargée : {file_path}")

    if user_id not in pending_images:
        pending_images[user_id] = {"files": [], "timestamp": datetime.now()}
    pending_images[user_id]["files"].append(file_path)

bot_app = Application.builder().token(BOT_TOKEN).build()
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))
bot_app.job_queue.run_repeating(handle_pending, interval=REPLY_DELAY * 60)

@app_fastapi.post("/webhook")
async def telegram_webhook(req: Request):
    body = await req.json()
    update = Update.de_json(body, bot_app.bot)
    await bot_app.process_update(update)
    return {"status": "ok"}

@app_fastapi.on_event("startup")
async def startup_event():
    await bot_app.initialize()
    await bot_app.start()
    print("🟢 Bot Telegram prêt à recevoir les mises à jour via webhook")

if __name__ == "__main__":
    print("🚀 Lancement local du serveur webhook sur http://localhost:8000")
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8000, reload=True)