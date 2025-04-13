import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, filters
)
import asyncio
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
import gspread
from google.oauth2.service_account import Credentials

# === CONFIGURATION ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = -1002317321058
REPLY_DELAY = 3  # minutes

# === GOOGLE SHEET ===
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=SCOPES)
gc = gspread.authorize(credentials)
sheet = gc.open_by_key("1__RzRpZKj0kg8Cl0QB-D91-hGKKff9SqsOQRE0GvReE")
worksheet = sheet.worksheet("Données Journalières")

# === TEMP DATA ===
pending_images = {}

# === FASTAPI ===
app_fastapi = FastAPI()

# === OCR LOGIC ===
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
        if any(x in text.lower() for x in ["followers", "abonnés", "suivis", "publications"]):
            return text
    return None

def extract_info_from_image(image_path):
    text = try_ocr_variants(image_path)
    if not text:
        return "Inconnu", "ECHEC OCR ❌", -1

    lines = text.split("\n")
    text_lower = text.lower()
    if "threads" in text_lower:
        network = "Threads"
    elif "tiktok" in text_lower or "j'aime" in text_lower:
        network = "TikTok"
    elif "twitter" in text_lower or "tweets" in text_lower:
        network = "Twitter"
    else:
        network = "Instagram"

    account, followers = "inconnu", -1
    for line in lines:
        if "@" in line and account == "inconnu":
            account = line.strip().split()[0]
        if any(x in line.lower() for x in ["followers", "abonnés"]):
            digits = ''.join([c if c.isdigit() or c in "kKmM.," else '' for c in line]).replace(",", ".")
            if 'k' in digits.lower():
                followers = int(float(digits.lower().replace('k','')) * 1000)
            elif 'm' in digits.lower():
                followers = int(float(digits.lower().replace('m','')) * 1_000_000)
            elif digits:
                followers = int(float(digits))

    if account == "inconnu" or followers == -1:
        return "Inconnu", "ECHEC OCR ❌", -1

    return network, account, followers

def get_previous_count(account_name):
    try:
        records = worksheet.get_all_records()
        for row in reversed(records):
            if row['Compte'] == account_name and row['Abonnés'] > 0:
                return row['Abonnés']
    except:
        pass
    return 0

# === TELEGRAM ===
bot_app = ApplicationBuilder().token(BOT_TOKEN).build()

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

bot_app.add_handler(MessageHandler(filters.PHOTO, handle_image))

async def handle_pending(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for user_id in list(pending_images.keys()):
        images = pending_images[user_id]
        if (now - images["timestamp"]).total_seconds() > REPLY_DELAY * 60:
            results = []
            for file_path in images["files"]:
                res = extract_info_from_image(file_path)
                today = datetime.now().strftime("%Y-%m-%d")
                try:
                    all_rows = worksheet.get_all_records()
                    if any(r.get('Date') == today and r.get('Compte') == res[1] for r in all_rows):
                        continue
                except Exception as e:
                    print("Erreur lecture Google Sheet :", e)
                    continue

                previous = get_previous_count(res[1])
                evolution = res[2] - previous if res[2] > 0 else 0
                try:
                    worksheet.append_row([
                        today,
                        context.bot.get_chat(user_id).username or "@inconnu",
                        res[0],
                        res[1],
                        res[2],
                        evolution
                    ])
                    results.append(res)
                except Exception as e:
                    print("Erreur écriture Google Sheet :", e)

            msg = f"🤖 {datetime.now().strftime('%d/%m')} – {len(results)} comptes détectés et ajoutés ✅"
            try:
                await context.bot.send_message(chat_id=user_id, text=msg)
            except Exception as e:
                print("Erreur message Telegram :", e)
            del pending_images[user_id]

# === LANCEMENT BOT À STARTUP FASTAPI ===
async def start_bot():
    await bot_app.initialize()
    await bot_app.start()
    bot_app.job_queue.run_repeating(handle_pending, interval=REPLY_DELAY * 60)
    print("🟢 Bot Telegram prêt à recevoir les mises à jour via webhook")

@app_fastapi.on_event("startup")
async def startup_event():
    asyncio.create_task(start_bot())

# === ENDPOINT FASTAPI POUR WEBHOOK TELEGRAM ===
@app_fastapi.post("/webhook")
async def telegram_webhook(req: Request):
    try:
        body = await req.json()
        update = Update.de_json(body, bot_app.bot)
        await bot_app.process_update(update)
    except Exception as e:
        print("Erreur webhook FastAPI :", e)
    return {"status": "ok"}

# === POUR LANCEMENT LOCAL (utile en dev uniquement) ===
if __name__ == "__main__":
    import uvicorn
    print("🚀 Lancement local du serveur webhook sur http://localhost:8000")
    uvicorn.run("main:app_fastapi", host="0.0.0.0", port=8000, reload=True)
