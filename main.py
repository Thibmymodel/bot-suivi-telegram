# === main.py ===
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
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    CommandHandler,
    filters,
    Application
)
from fastapi import FastAPI, Request
import uvicorn
import asyncio

# === CONFIGURATION ===
app_fastapi = FastAPI()
BOT_TOKEN = "7627601916:AAHoCOA3MxpHQxjSz4WA2eIvWJrby6ty0d4"
GROUP_ID = -1002317321058
REPLY_DELAY = 5  # minutes

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
credentials_dict = json.loads(credentials_json)
credentials = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key("1__RzRpZKj0kg8Cl0QB-D91-hGKKff9SqsOQRE0GvReE")
worksheet = sheet.worksheet("Données Journalières")

pending_images = {}

# === OCR ADAPTATIF ===
def try_ocr_variants(image_path):
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
        if any(x in text.lower() for x in ["followers", "abonnés", "suivi(e)", "publications"]):
            return text
    return None

# === EXTRACTION INFO ===
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
        elif "twitter" in text_lower or ("abonnés" in text_lower and "rejoint x" in text_lower):
            network = "Twitter"
        elif "followers" in text_lower or "suivi(e)" in text_lower:
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

# === ÉVOLUTION J-1 ===
def get_previous_count(account_name):
    try:
        records = worksheet.get_all_records()
        for row in reversed(records):
            if row['Compte'] == account_name and row['Abonnés'] > 0:
                return row['Abonnés']
    except:
        pass
    return 0

# === TÂCHE DE TRAITEMENT DES IMAGES ===
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

                worksheet.append_row([
                    today,
                    context.bot.get_chat(user_id).username or "@inconnu",
                    res[0],
                    res[1],
                    res[2],
                    evolution
                ])
                results.append(res)

            msg = f"🤖 {datetime.now().strftime('%d/%m')} – {len(results)} comptes détectés et ajoutés ✅"
            await context.bot.send_message(chat_id=user_id, text=msg)
            del pending_images[user_id]

# === GESTION IMAGES ===
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

# === BOT TELEGRAM ===
bot_app = Application.builder().token(BOT_TOKEN).build()
bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))
bot_app.job_queue.run_repeating(handle_pending, interval=REPLY_DELAY * 60)

async def run_bot():
    await bot_app.initialize()
    await bot_app.start()
    print("🪀 Bot Telegram en marche (mode webhook)")

asyncio.get_event_loop().create_task(run_bot())

@app_fastapi.post("/webhook")
async def telegram_webhook(req: Request):
    body = await req.json()
    update = Update.de_json(body, bot_app.bot)
    await bot_app.process_update(update)
    return {"status": "ok"}

# === LANCEMENT LOCAL (DEBUG) ===
if __name__ == "__main__":
    print("🚀 Lancement local sur http://localhost:8000")
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8000, reload=True)
