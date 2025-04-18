# [...] (imports & setup inchangés)

# --- Extraction followers dédiée TikTok ---
def extraire_followers_tiktok(texte_ocr: str) -> str | None:
    lignes = texte_ocr.replace(",", ".").split()
    nombres = []

    for mot in lignes:
        mot_clean = re.sub(r"[^\d.]", "", mot)
        if mot_clean:
            try:
                if "k" in mot.lower():
                    mot_clean = mot_clean.replace("k", "")
                    nombre = float(mot_clean) * 1000
                else:
                    nombre = float(mot_clean)
                nombres.append(int(nombre))
            except:
                continue

    if len(nombres) >= 2:
        return str(nombres[1])  # 2e bloc numérique = followers
    return None

# --- handle_photo ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.photo:
            return

        thread_id = message.message_thread_id
        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created"):
            return

        topic_name = reply.forum_topic_created.name
        if not topic_name.startswith("SUIVI "):
            return

        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        photo = message.photo[-1]

        file = await bot.get_file(photo.file_id)
        img_bytes = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(img_bytes))
        width, height = image.size
        cropped = image.crop((0, 0, width, int(height * 0.4)))
        enhanced = ImageOps.autocontrast(cropped)

        text = pytesseract.image_to_string(enhanced)
        logger.info(f"🔍 OCR brut :\n{text}")

        if "getallmylinks.com" in text.lower():
            reseau = "instagram"
        elif "beacons.ai" in text.lower():
            reseau = "twitter"
        elif "tiktok" in text.lower() or any(k in text.lower() for k in ["followers", "j'aime"]):
            reseau = "tiktok"
        elif "threads" in text.lower():
            reseau = "threads"
        elif any(x in text.lower() for x in ["modifier le profil", "suivi(e)s", "publications"]):
            reseau = "instagram"
        else:
            reseau = "instagram"

        usernames = re.findall(r"@([a-zA-Z0-9_.]{3,})", text)
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé"
        for u in usernames:
            matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.6)
            if matches:
                username = matches[0]
                break
        if username == "Non trouvé" and usernames:
            username = usernames[0]

        urls = re.findall(r"(getallmylinks|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.]+)", text)
        for _, u in urls:
            match = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.6)
            if match:
                username = match[0]
                break
            username = u

        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : '{username}' (réseau : {reseau})")

        abonnés = None
        if reseau == "tiktok":
            abonnés = extraire_followers_tiktok(text)
        else:
            pattern_three_numbers = re.compile(r"(\d{1,3}(?:[ .,]\d{3})?)\s+(\d{1,3}(?:[ .,]\d{3})?)\s+(\d{1,3}(?:[ .,]\d{3})?)")
            match = pattern_three_numbers.search(text.replace("\n", " "))
            if match:
                abonnés = match.group(2).replace(" ", "").replace(".", "").replace(",", "")

            if not abonnés:
                pattern_stats = re.compile(r"(\d{1,3}(?:[ .,]\d{3})*)(?=\s*(followers|abonn[ée]s?|j'aime|likes))", re.IGNORECASE)
                match = pattern_stats.search(text.replace("\n", " "))
                if match:
                    abonnés = match.group(1).replace(" ", "").replace(".", "").replace(",", "")

        if not username or not abonnés:
            raise ValueError("Nom d'utilisateur ou abonnés introuvable dans l'OCR")

        if message.message_id in already_processed:
            logger.info("⚠️ Message déjà traité, on ignore.")
            return
        already_processed.add(message.message_id)

        today = datetime.datetime.now().strftime("%d/%m/%Y")
        row = [today, assistant, reseau, f"@{username}", abonnés, ""]
        sheet.append_row(row)

        msg = f"🦠 {today} - {assistant} - 1 compte détecté et ajouté ✅"
        await bot.send_message(chat_id=GROUP_ID, text=msg)

    except Exception as e:
        logger.exception("❌ Erreur traitement handle_photo")
        await bot.send_message(chat_id=GROUP_ID, text=f"❌ {datetime.datetime.now().strftime('%d/%m')} - Analyse OCR impossible")

# --- reste du code (webhook, FastAPI, etc.) inchangé ---
