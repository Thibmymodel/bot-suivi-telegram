import json
import io
import re
import datetime
import logging
import os
import traceback # Assurez-vous que traceback est importé
from difflib import get_close_matches

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from PIL import Image, ImageOps
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.cloud import vision

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("TELEGRAM_GROUP_ID")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Initialisation Google Sheets
google_creds_gspread_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_GSPREAD")
if not google_creds_gspread_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS_GSPREAD n_est pas définie.")
    exit()
try:
    creds_gspread_dict = json.loads(google_creds_gspread_json_str)
    gspread_creds = ServiceAccountCredentials.from_service_account_info(creds_gspread_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(gspread_creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
    logger.info("Connexion à Google Sheets réussie.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Sheets: {e}")
    logger.error(traceback.format_exc())
    exit()

# Initialisation Google Vision AI
google_creds_vision_json_str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not google_creds_vision_json_str:
    logger.error("La variable d_environnement GOOGLE_APPLICATION_CREDENTIALS (pour Vision) n_est pas définie.")
    exit()
try:
    creds_vision_dict = json.loads(google_creds_vision_json_str)
    vision_creds = ServiceAccountCredentials.from_service_account_info(creds_vision_dict)
    vision_client = vision.ImageAnnotatorClient(credentials=vision_creds)
    logger.info("Client Google Vision AI initialisé avec succès.")
except Exception as e:
    logger.error(f"Erreur lors de l_initialisation de Google Vision AI: {e}")
    logger.error(traceback.format_exc())
    exit()

bot = Bot(TOKEN)
already_processed = set()

with open("known_handles.json", "r", encoding="utf-8") as f:
    KNOWN_HANDLES = json.load(f)

def corriger_username(username: str, reseau: str) -> str:
    if reseau == "instagram" and username.startswith("@"):
        # For Instagram, the @ is often included in OCR but known_handles might not have it,
        # or the desired format in sheets is without it.
        return username[1:]
    if reseau == "threads" and not username.startswith("@") and username != "Non trouvé" and username != "":
        return f"@{username}"
    # For other networks like Twitter, TikTok, the @ is usually expected and handled by initial regex or known_handles.
    return username

def normaliser_nombre_followers(nombre_str: str) -> str | None:
    if not isinstance(nombre_str, str):
        return None
    # Supprimer les espaces avant toute autre vérification pour gérer "2 500" -> "2500"
    nombre_str_test = nombre_str.replace(" ", "").strip()

    # Regex pour valider le format général (chiffres, points, virgules, k/K, m/M)
    # Permet les points et virgules comme séparateurs de milliers ou décimaux avant k/M
    if not re.match(r"^[\d.,]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: 	'{nombre_str}' (nettoyé en '{nombre_str_test}') ne correspond pas au format attendu après suppression des espaces.")
        return None

    nombre_str_clean = nombre_str_test.lower()
    valeur = None

    try:
        if "k" in nombre_str_clean:
            # Supprimer les points et virgules avant de traiter le 'k'
            # Exemple: "1.2k" -> "12k", "1,2k" -> "12k"
            # Puis "12k" -> 1200. "1k" -> 1000
            num_part = nombre_str_clean.replace("k", "").replace(",", ".") # Unifier les séparateurs décimaux en point
            if not re.match(r"^\d*\.?\d+$", num_part): # Doit être un nombre valide avant 'k'
                logger.debug(f"normaliser_nombre_followers: Format 'k' invalide pour '{nombre_str_clean}' (partie numérique: '{num_part}')")
                return None
            valeur = str(int(float(num_part) * 1000))
        elif "m" in nombre_str_clean:
            # Similaire à 'k'
            num_part = nombre_str_clean.replace("m", "").replace(",", ".")
            if not re.match(r"^\d*\.?\d+$", num_part):
                logger.debug(f"normaliser_nombre_followers: Format 'm' invalide pour '{nombre_str_clean}' (partie numérique: '{num_part}')")
                return None
            valeur = str(int(float(num_part) * 1000000))
        else:
            # Si pas de k/m, supprimer tous les non-chiffres (points et virgules utilisés comme séparateurs de milliers)
            # Exemple: "2.500" -> "2500", "2,500" -> "2500"
            nombre_final_digits = re.sub(r"[^\d]", "", nombre_str_clean)
            if not nombre_final_digits.isdigit():
                logger.debug(f"normaliser_nombre_followers: '{nombre_final_digits}' (venant de '{nombre_str_clean}') n'est pas un digit après nettoyage final.")
                return None
            valeur = str(int(nombre_final_digits))
    except ValueError as e:
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de '{nombre_str_clean}' (original: '{nombre_str}'): {e}")
        return None
    return valeur

def extraire_followers_spatial(text_annotations, mots_cles_specifiques, reseau_nom="inconnu") -> str | None:
    try:
        logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Début de l_extraction spatiale ---")
        keyword_annotations_list = []
        number_annotations_list = []

        if not text_annotations:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucune annotation de texte fournie.")
            return None
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre total d_annotations reçues: {len(text_annotations)}")
        if len(text_annotations) > 1:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Premières annotations (description et position Y moyenne):")
            for i, annotation in enumerate(text_annotations[1:6]): 
                try:
                    if hasattr(annotation, 'description') and hasattr(annotation, 'bounding_poly') and hasattr(annotation.bounding_poly, 'vertices') and len(annotation.bounding_poly.vertices) >=4:
                        vertices = annotation.bounding_poly.vertices
                        avg_y_log = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                        logger.info(f"  - Ann {i+1}: '{annotation.description}' (avg_y: {avg_y_log})")
                    else:
                        logger.warning(f"extraire_followers_spatial ({reseau_nom}): Annotation initiale {i+1} malformée: {annotation}")
                except Exception as e_log_ann:
                    logger.error(f"extraire_followers_spatial ({reseau_nom}): Erreur lors du logging de l_annotation initiale {i+1}: {e_log_ann}. Annotation: {annotation}")

        for i, annotation in enumerate(text_annotations[1:]): # Ignorer la première annotation (texte complet)
            try:
                if not hasattr(annotation, 'description') or not hasattr(annotation, 'bounding_poly'):
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}): Annotation {i} n_a pas les attributs requis (description/bounding_poly), ignorée. Contenu: {annotation}")
                    continue

                text = annotation.description.lower().strip()
                
                if not hasattr(annotation.bounding_poly, 'vertices') or len(annotation.bounding_poly.vertices) < 4:
                    logger.warning(f"extraire_followers_spatial ({reseau_nom}): Annotation {i} ('{text}') n_a pas de bounding_poly.vertices valides, ignorée.")
                    continue
                    
                vertices = annotation.bounding_poly.vertices
                avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4
                logger.debug(f"extraire_followers_spatial ({reseau_nom}): Traitement annotation {i}: '{text}' (avg_y={avg_y}, avg_x={avg_x})")

                if any(keyword.lower() in text for keyword in mots_cles_specifiques):
                    keyword_annotations_list.append({"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                    logger.info(f"extraire_followers_spatial ({reseau_nom}): MOT-CLÉ TROUVÉ: '{text}' à y={avg_y}, x={avg_x}")
                
                # Check if text contains a digit and broadly matches number format (before normalization)
                # This regex allows spaces within the number string initially, normaliser_nombre_followers will handle them.
                if re.search(r"\d", text) and re.match(r"^[\d.,\s]*[kKm]?$", text, re.IGNORECASE):
                    nombre_normalise_test = normaliser_nombre_followers(text) # Test normalization
                    if nombre_normalise_test:
                        # Avoid matching time-like strings as numbers, e.g., "10:30"
                        if not re.fullmatch(r"\d{1,2}:\d{2}", text.replace(" ", "")):
                            number_annotations_list.append({"text": text, "normalized": nombre_normalise_test, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): NOMBRE POTENTIEL TROUVÉ: '{text}' (normalisé: {nombre_normalise_test}) à y={avg_y}, x={avg_x}")
                        else:
                            logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre '{text}' ignoré (format heure).")
                    else:
                        logger.debug(f"extraire_followers_spatial ({reseau_nom}): '{text}' non normalisable en nombre.")
                else:
                    logger.debug(f"extraire_followers_spatial ({reseau_nom}): Annotation '{text}' ne semble pas être un nombre (basé sur regex), ignorée pour la normalisation.")
            except Exception as e_loop_ann:
                logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR INATTENDUE lors du traitement de l_annotation {i}: {e_loop_ann}")
                logger.error(f"extraire_followers_spatial ({reseau_nom}): Annotation problématique: {annotation}")
                logger.error(traceback.format_exc())
                continue 

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Fin de la boucle d_analyse des annotations.")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de mots-clés trouvés: {len(keyword_annotations_list)}")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de nombres potentiels initiaux: {len(number_annotations_list)}")

        # --- BEGIN NEW MERGE LOGIC FOR ADJACENT NUMBERS ---
        if len(number_annotations_list) > 1:
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Tentative de fusion des nombres adjacents.")
            temp_sorted_numbers = sorted(number_annotations_list, key=lambda ann: (round(ann['avg_y'] / 15), ann['avg_x'])) # Group by similar Y lines (tolerance 15px)
            
            merged_numbers_final = []
            visited_indices = [False] * len(temp_sorted_numbers)
            
            for i in range(len(temp_sorted_numbers)):
                if visited_indices[i]:
                    continue
                
                current_ann_data = temp_sorted_numbers[i]
                # Use original text for merging, normalized value will be recalculated
                merged_text_parts_original = [current_ann_data['text']] 
                
                last_merged_ann_data = current_ann_data
                visited_indices[i] = True
                
                current_chain_merged_successfully = False

                for j in range(i + 1, len(temp_sorted_numbers)):
                    if visited_indices[j]:
                        continue
                    
                    next_ann_data = temp_sorted_numbers[j]
                    
                    y_diff = abs(last_merged_ann_data['avg_y'] - next_ann_data['avg_y'])
                    # x_diff should represent the gap between the end of last_merged_ann and start of next_ann
                    # Approximating with avg_x difference for now, assuming typical text height/width ratios
                    # A more robust method would use bounding_poly.vertices
                    x_diff_avg = next_ann_data['avg_x'] - last_merged_ann_data['avg_x'] 
                    
                    # Thresholds: very close on Y, next is to the right and not too far horizontally.
                    # x_diff_avg < 80 implies they are relatively close horizontally. Max Y diff of 20px.
                    if y_diff < 25 and 0 < x_diff_avg < 80: 
                        # Try to concatenate original texts and then normalize
                        # This handles cases like lized']) >=0]
                    if valid_numbers:
                        valid_numbers.sort(reverse=True)
                        logger.info(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Nombres valides triés par valeur: {valid_numbers}")
                        best_fallback_candidate = str(valid_numbers[0])
                        logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Sélection du plus grand nombre valide: {best_fallback_candidate}")
                        return best_fallback_candidate
                    else:
                        logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Aucun nombre valide (après normalisation et filtrage) à retourner.")
                except Exception as e_fallback_sort:
                     logger.error(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Erreur lors du tri/sélection du fallback: {e_fallback_sort}")
            logger.warning(f"extraire_followers_spatial ({reseau_nom}) (Fallback final): Aucun nombre à retourner.")
            return None

    except Exception as e_global_spatial:
        logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR GLOBALE INATTENDUE DANS LA FONCTION: {e_global_spatial}")
        logger.error(traceback.format_exc()) 
        return None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    message_status_general = None 
    donnees_extraites_ok = False
    reply_message_exists_for_error_handling = False
    topic_name_for_error_handling = ""
    username = "Non trouvé"
    reseau = "instagram" 
    abonnés = None
    action_tentee = False

    try:
        message = update.message
        if not message or not message.photo:
            logger.info("handle_photo: Message None ou sans photo. Aucune action.")
            return

        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created") or not reply.forum_topic_created:
            logger.info("handle_photo: Pas une réponse à un topic valide. Aucune action.")
            return
        
        action_tentee = True
        reply_message_exists_for_error_handling = True
        topic_name = reply.forum_topic_created.name
        topic_name_for_error_handling = topic_name

        if not topic_name.startswith("SUIVI "):
            logger.info(f"handle_photo: Nom du topic 	'{topic_name}' non conforme. Aucune action.")
            return
        
        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        logger.info(f"handle_photo: Assistant extrait: 	'{assistant}'")
        message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌"

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        img_bytes_io = io.BytesIO()
        await file.download_to_memory(img_bytes_io)
        img_bytes_io.seek(0)
        img_content = img_bytes_io.read()

        image = Image.open(io.BytesIO(img_content))
        width, height = image.size
        cropped_image = image.crop((0, 0, width, int(height * 0.4)))
        enhanced_image = ImageOps.autocontrast(cropped_image)
        byte_arr = io.BytesIO()
        enhanced_image.save(byte_arr, format='PNG')
        content_vision = byte_arr.getvalue()

        image_vision = vision.Image(content=content_vision)
        response = vision_client.text_detection(image=image_vision)
        texts_annotations_vision = response.text_annotations

        if response.error.message:
            logger.error(f"handle_photo: Erreur API Google Vision: {response.error.message}")
            return

        ocr_text_full = texts_annotations_vision[0].description if texts_annotations_vision and len(texts_annotations_vision) > 0 else ""
        logger.info(f"🔍 OCR Google Vision brut (premiers 500 caractères):\n{ocr_text_full[:500]}")

        if not ocr_text_full:
            logger.warning("handle_photo: OCR n_a retourné aucun texte.")
            return

        # Identification du réseau
        # Amélioration de la détection de réseau
        ocr_lower = ocr_text_full.lower()
        if "tiktok" in ocr_lower or "j_aime" in ocr_lower: # "j'aime" est assez spécifique à TikTok
            reseau = "tiktok"
        elif "twitter" in ocr_lower or "tweets" in ocr_lower or "reposts" in ocr_lower or "abonnement" in ocr_lower: # "Abonnements" est commun sur Twitter
            reseau = "twitter"
        elif "instagram" in ocr_lower or "publications" in ocr_lower or "getallmylinks.com" in ocr_lower or "modifier le profil" in ocr_lower:
            reseau = "instagram"
        elif "threads" in ocr_lower:
            reseau = "threads"
        elif "beacons.ai" in ocr_lower: # Souvent utilisé pour des liens Instagram/Twitter
            if "twitter" in ocr_lower: reseau = "twitter"
            else: reseau = "instagram" # Par défaut si beacons.ai mais pas de mention claire de Twitter
        else: 
            reseau = "instagram" 
            logger.info("Réseau non clairement identifié, par défaut Instagram.")
        logger.info(f"handle_photo: Réseau identifié: {reseau}")

        usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text_full)
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé"
        cleaned_usernames = [re.sub(r"[^a-zA-Z0-9_.-]", "", u).lower() for u in usernames_found]
        for u_cleaned in cleaned_usernames:
            if u_cleaned in [h.lower() for h in reseau_handles]:
                username = next((h_original for h_original in reseau_handles if h_original.lower() == u_cleaned), "Non trouvé")
                if username != "Non trouvé": break
        if username == "Non trouvé":
            for u in usernames_found:
                matches = get_close_matches(u.lower(), reseau_handles, n=1, cutoff=0.7)
                if matches: username = matches[0]; break
        if username == "Non trouvé" and usernames_found: username = usernames_found[0]
        urls = re.findall(r"(getallmylinks\.com|beacons\.ai|linktr\.ee|tiktok\.com)/([a-zA-Z0-9_.-]+)", ocr_text_full, re.IGNORECASE)
        if username == "Non trouvé" and urls:
            for _, u_from_url in urls:
                match_url = get_close_matches(u_from_url.lower(), reseau_handles, n=1, cutoff=0.7)
                if match_url: username = match_url[0]; break
                if username == "Non trouvé": username = u_from_url
        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : 	'{username}' (réseau : {reseau})")

        abonnés = None
        if reseau == "tiktok":
            mots_cles_tiktok = ["followers", "abonnés", "abonné", "fans", "abos"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_tiktok, "tiktok")
        elif reseau == "instagram":
            mots_cles_instagram = ["followers", "abonnés", "abonné", "suivi(e)s", "suivis"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_instagram, "instagram")
        elif reseau == "twitter":
            mots_cles_twitter = ["abonnés", "abonné", "followers", "suivies", "suivis", "abonnements"] # "Abonnements" pour le nombre de personnes suivies par le compte, "Abonnés" pour les followers
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_twitter, "twitter")
        elif reseau == "threads":
             mots_cles_threads = ["followers", "abonnés", "abonné"]
             abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_threads, "threads")
        else: # Fallback générique si le réseau est mal identifié mais qu_on tente quand même
            mots_cles_generiques = ["followers", "abonnés", "abonné", "fans", "suivi(e)s", "suivis"]
            abonnés = extraire_followers_spatial(texts_annotations_vision, mots_cles_generiques, f"générique ({reseau})")

        logger.info(f"handle_photo: Abonnés extraits ({reseau}): {abonnés}")

        if not username or username == "Non trouvé" or not abonnés:
            logger.warning(f"Données incomplètes: Username ('{username}') ou Abonnés ('{abonnés}') pour {reseau}.")
            donnees_extraites_ok = False
        else:
            donnees_extraites_ok = True

        if message.message_id in already_processed:
            logger.info(f"⚠️ Message ID {message.message_id} déjà traité, on ignore.")
            return
        already_processed.add(message.message_id)

        if donnees_extraites_ok:
            username_to_sheet = f"@{username}"
            abonnés_to_sheet = str(abonnés)
            row = [today, assistant, reseau, username_to_sheet, abonnés_to_sheet, ""]
            try:
                sheet.append_row(row)
                logger.info("handle_photo: Ligne ajoutée à Google Sheets.")
                message_status_general = f"🤖 {today} - {assistant} - ✅ 1 compte détecté et ajouté ✅"
            except Exception as e_sheet:
                logger.error(f"handle_photo: Erreur lors de l_ajout à Google Sheets: {e_sheet}")
                logger.error(traceback.format_exc())
                message_status_general = f"🤖 {today} - {assistant} - ⚠️ Erreur écriture Sheets ⚠️"
        
    except Exception as e:
        logger.error("❌ Erreur globale dans handle_photo")
        logger.error(traceback.format_exc())
        assistant_nom_erreur = assistant
        if assistant == "INCONNU" and reply_message_exists_for_error_handling and topic_name_for_error_handling.startswith("SUIVI "):
            assistant_nom_erreur = topic_name_for_error_handling.replace("SUIVI ", "").strip().upper()
        message_status_general = f"🤖 {today} - {assistant_nom_erreur} - ❌ Analyse OCR impossible ❌"

    finally:
        if action_tentee and message_status_general:
            logger.info(f"Message à envoyer au General: {message_status_general}")
            try:
                await bot.send_message(chat_id=GROUP_ID, text=message_status_general)
                logger.info("Message de statut envoyé au sujet General.")
            except Exception as e_send_general:
                logger.error(f"Impossible d_envoyer le message de statut au sujet General: {e_send_general}")
                logger.error(traceback.format_exc())
        else:
            logger.info("Aucune action de traitement d_image n_a été tentée ou aucun message de statut à envoyer.")
        logger.info("--- Sortie de handle_photo ---")

from fastapi import FastAPI, Request, HTTPException
import asyncio
import uvicorn

app = FastAPI(lifespan=None)

@app.on_event("startup")
async def startup():
    logger.info("Application startup...")
    mode_polling = os.getenv("MODE_POLLING", "false").lower()
    if mode_polling != "true":
        base_webhook_url = os.getenv("RAILWAY_PUBLIC_URL")
        if base_webhook_url:
            normalized_webhook_url = base_webhook_url.rstrip('/') + "/webhook"
            logger.info(f"Setting webhook to: {normalized_webhook_url}")
            await bot.set_webhook(url=normalized_webhook_url, allowed_updates=Update.ALL_TYPES)
            logger.info("Webhook set.")
        else:
            logger.warning("RAILWAY_PUBLIC_URL not set, webhook not configured.")
    else:
        logger.info("Mode polling activé, pas de configuration de webhook.")

@app.post("/webhook")
async def webhook_handler(request: Request):
    logger.info("--- Entrée dans webhook_handler ---")
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        
        if update.message and update.message.photo:
            context = ContextTypes.DEFAULT_TYPE(application=None, chat_id=update.effective_chat.id if update.effective_chat else None, user_id=update.effective_user.id if update.effective_user else None)
            await handle_photo(update, context)
        else:
            logger.info("webhook_handler: Message reçu sans photo, ignoré.")
            
        return {"status": "ok"}
    except json.JSONDecodeError:
        logger.error("Error decoding JSON from webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error("Error processing webhook")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        logger.info("--- Sortie de webhook_handler ---")

if __name__ == "__main__":
    mode_polling = os.getenv("MODE_POLLING", "false").lower()
    if mode_polling == "true":
        logger.info("Lancement en mode polling...")
        application = Application.builder().token(TOKEN).build()
        application.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), handle_photo))
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        logger.info("Lancement en mode webhook avec Uvicorn (localement)...")
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)