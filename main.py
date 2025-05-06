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
        return username[1:]
    return username

def normaliser_nombre_followers(nombre_str: str) -> str | None:
    if not isinstance(nombre_str, str):
        return None
    # Permettre les espaces dans la chaîne initiale pour plus de flexibilité
    nombre_str_test = nombre_str.strip()
    if not re.match(r"^[\d.,\s]*[kKm]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_test}	' ne correspond pas au format attendu.")
        return None

    nombre_str_clean = nombre_str_test.lower().replace(" ", "").replace(".", "").replace(",", "")
    valeur = None
    try:
        if "k" in nombre_str_clean:
            # S_assurer qu_il y a des chiffres avant le 'k'
            if not re.match(r"^\d+k$", nombre_str_clean):
                logger.debug(f"normaliser_nombre_followers: Format 'k' invalide pour 	'{nombre_str_clean}	'")
                return None
            valeur = str(int(float(nombre_str_clean.replace("k", "")) * 1000))
        elif "m" in nombre_str_clean:
            # S_assurer qu_il y a des chiffres avant le 'm'
            if not re.match(r"^\d+m$", nombre_str_clean):
                logger.debug(f"normaliser_nombre_followers: Format 'm' invalide pour 	'{nombre_str_clean}	'")
                return None
            valeur = str(int(float(nombre_str_clean.replace("m", "")) * 1000000))
        else:
            if not nombre_str_clean.isdigit():
                logger.debug(f"normaliser_nombre_followers: 	'{nombre_str_clean}	' n_est pas un digit après nettoyage.")
                return None
            valeur = str(int(nombre_str_clean))
    except ValueError:
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de 	'{nombre_str_clean}	'")
        return None
    return valeur

def extraire_followers_tiktok(text_annotations) -> str | None:
    try: 
        logger.info("extraire_followers_tiktok: --- Début de l_extraction TikTok détaillée ---")
        followers_keyword_annotations = []
        number_annotations = []

        if not text_annotations:
            logger.warning("extraire_followers_tiktok: Aucune annotation de texte fournie.")
            return None
        
        logger.info(f"extraire_followers_tiktok: Nombre total d_annotations reçues: {len(text_annotations)}")
        if len(text_annotations) > 1:
            logger.info("extraire_followers_tiktok: Premières annotations (description et position Y moyenne):")
            for i, annotation in enumerate(text_annotations[1:6]): 
                try:
                    if hasattr(annotation, 'description') and hasattr(annotation, 'bounding_poly') and hasattr(annotation.bounding_poly, 'vertices') and len(annotation.bounding_poly.vertices) >=4:
                        vertices = annotation.bounding_poly.vertices
                        avg_y_log = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                        logger.info(f"  - Ann {i+1}: 	'{annotation.description}	' (avg_y: {avg_y_log})")
                    else:
                        logger.warning(f"extraire_followers_tiktok: Annotation initiale {i+1} malformée: {annotation}")
                except Exception as e_log_ann:
                    logger.error(f"extraire_followers_tiktok: Erreur lors du logging de l_annotation initiale {i+1}: {e_log_ann}. Annotation: {annotation}")

        for i, annotation in enumerate(text_annotations[1:]):
            try:
                if not hasattr(annotation, 'description') or not hasattr(annotation, 'bounding_poly'):
                    logger.warning(f"extraire_followers_tiktok: Annotation {i} n_a pas les attributs requis (description/bounding_poly), ignorée. Contenu: {annotation}")
                    continue

                text = annotation.description.lower().strip()
                
                if not hasattr(annotation.bounding_poly, 'vertices') or len(annotation.bounding_poly.vertices) < 4:
                    logger.warning(f"extraire_followers_tiktok: Annotation {i} ('{text}') n_a pas de bounding_poly.vertices valides, ignorée.")
                    continue
                    
                vertices = annotation.bounding_poly.vertices
                avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4
                logger.debug(f"extraire_followers_tiktok: Traitement annotation {i}: 	'{text}	' (avg_y={avg_y}, avg_x={avg_x})")

                if any(keyword in text for keyword in ["followers", "abonnés", "abonné", "fans", "abos"]):
                    followers_keyword_annotations.append({"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                    logger.info(f"extraire_followers_tiktok: MOT-CLÉ TROUVÉ: 	'{text}	' à y={avg_y}, x={avg_x}")
                
                # Vérification plus stricte pour les nombres
                # Doit contenir au moins un chiffre, peut avoir des points/virgules/espaces et k/m
                if re.search(r"\d", text) and re.match(r"^[\d.,\s]*[kKm]?$", text, re.IGNORECASE):
                    nombre_normalise_test = normaliser_nombre_followers(text)
                    if nombre_normalise_test:
                        if not re.fullmatch(r"\d{1,2}:\d{2}", text): # Exclure les heures
                            number_annotations.append({"text": text, "normalized": nombre_normalise_test, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                            logger.info(f"extraire_followers_tiktok: NOMBRE POTENTIEL TROUVÉ: 	'{text}	' (normalisé: {nombre_normalise_test}) à y={avg_y}, x={avg_x}")
                        else:
                            logger.info(f"extraire_followers_tiktok: Nombre 	'{text}	' ignoré (format heure).")
                    else:
                        logger.debug(f"extraire_followers_tiktok: 	'{text}	' non normalisable en nombre.")
                else:
                    logger.debug(f"extraire_followers_tiktok: Annotation 	'{text}	' ne semble pas être un nombre (basé sur regex), ignorée pour la normalisation.")
            except Exception as e_loop_ann:
                logger.error(f"extraire_followers_tiktok: ERREUR INATTENDUE lors du traitement de l_annotation {i}: {e_loop_ann}")
                logger.error(f"extraire_followers_tiktok: Annotation problématique: {annotation}")
                logger.error(traceback.format_exc())
                continue 

        logger.info("extraire_followers_tiktok: Fin de la boucle d_analyse des annotations.")
        logger.info(f"extraire_followers_tiktok: Nombre de mots-clés trouvés: {len(followers_keyword_annotations)}")
        logger.info(f"extraire_followers_tiktok: Nombre de nombres potentiels trouvés: {len(number_annotations)}")
        for idx, na in enumerate(number_annotations):
            logger.info(f"  - Nombre {idx}: {na['text']} (normalisé: {na['normalized']}) à y={na['avg_y']}")

        if not followers_keyword_annotations:
            logger.warning("extraire_followers_tiktok: Aucun mot-clé de followers trouvé. Tentative de fallback basée sur la position des nombres.")
            if len(number_annotations) >= 3:
                number_annotations.sort(key=lambda ann: ann['avg_x'])
                logger.info(f"extraire_followers_tiktok (Fallback): Nombres triés par X: {[na['text'] for na in number_annotations]}")
                if (abs(number_annotations[0]['avg_y'] - number_annotations[1]['avg_y']) < 20 and
                    abs(number_annotations[1]['avg_y'] - number_annotations[2]['avg_y']) < 20):
                    logger.info(f"extraire_followers_tiktok (Fallback): 3 nombres alignés trouvés. Sélection du 2ème: {number_annotations[1]['normalized']}")
                    return number_annotations[1]['normalized']
                else:
                    logger.warning("extraire_followers_tiktok (Fallback): Les 3 nombres ne sont pas alignés en Y.")
            else:
                logger.warning(f"extraire_followers_tiktok (Fallback): Pas assez de nombres ({len(number_annotations)}) pour le fallback des 3 nombres.")
            logger.warning("extraire_followers_tiktok: Conditions de fallback non remplies.")
            return None

        best_candidate = None
        min_distance = float('inf')

        logger.info("extraire_followers_tiktok: Recherche du meilleur candidat basé sur la proximité du mot-clé.")
        for kw_ann in followers_keyword_annotations:
            logger.info(f"  - Analyse pour mot-clé: 	'{kw_ann['text']}	' à y={kw_ann['avg_y']}")
            for num_ann in number_annotations:
                y_diff = kw_ann['avg_y'] - num_ann['avg_y']
                x_diff = abs(kw_ann['avg_x'] - num_ann['avg_x'])
                
                logger.debug(f"    - Comparaison avec nombre: 	'{num_ann['text']}	' (norm: {num_ann['normalized']}) à y={num_ann['avg_y']}. y_diff={y_diff:.2f}, x_diff={x_diff:.2f}")

                if y_diff > -20 and x_diff < 150: 
                    distance = (y_diff**2 + x_diff**2)**0.5
                    logger.debug(f"      Candidat potentiel. Distance: {distance:.2f}")
                    if distance < min_distance:
                        try:
                            current_num_val = int(num_ann['normalized'])
                            # Pour "followers", on s_attend à un nombre plus grand
                            if kw_ann['text'] == "followers" and current_num_val > 50: 
                                min_distance = distance
                                best_candidate = num_ann['normalized']
                                logger.info(f"      NOUVEAU MEILLEUR CANDIDAT (pour 'followers'): {best_candidate} (distance: {min_distance:.2f})")
                            # Pour les autres mots-clés (abonnés, etc.), on est moins strict sur la taille
                            elif kw_ann['text'] != "followers": 
                                min_distance = distance
                                best_candidate = num_ann['normalized']
                                logger.info(f"      NOUVEAU MEILLEUR CANDIDAT (pour '{kw_ann['text']}'): {best_candidate} (distance: {min_distance:.2f})")
                            else:
                                logger.debug(f"      Candidat 	'{num_ann['text']}	' non retenu pour 'followers' (valeur < 50 ou autre critère).")
                        except ValueError:
                            logger.warning(f"      Impossible de convertir 	'{num_ann['normalized']}	' en entier pour la comparaison.")
                    else:
                        logger.debug(f"      Distance {distance:.2f} non inférieure à min_distance {min_distance:.2f}.")
                else:
                    logger.debug(f"      Critères de position (y_diff > -20 ET x_diff < 150) non remplis.")
        
        if best_candidate:
            logger.info(f"extraire_followers_tiktok: Nombre de followers final extrait: {best_candidate}")
            return best_candidate
        else:
            logger.warning("extraire_followers_tiktok: Aucun candidat de followers n_a pu être sélectionné après analyse spatiale.")
            if number_annotations:
                # Fallback: prendre le plus grand nombre si aucun mot-clé n_a matché
                number_annotations.sort(key=lambda x: int(x.get("normalized", "0")), reverse=True)
                logger.info(f"extraire_followers_tiktok (Fallback final): Nombres triés par valeur: {[na['text'] for na in number_annotations]}")
                if number_annotations and number_annotations[0]['normalized']:
                     logger.warning(f"extraire_followers_tiktok (Fallback final): Sélection du plus grand nombre: {number_annotations[0]['normalized']}")
                     return number_annotations[0]['normalized']
            logger.warning("extraire_followers_tiktok (Fallback final): Aucun nombre à retourner.")
            return None

    except Exception as e_global_tiktok:
        logger.error(f"extraire_followers_tiktok: ERREUR GLOBALE INATTENDUE DANS LA FONCTION: {e_global_tiktok}")
        logger.error(traceback.format_exc()) # Log du traceback complet
        return None # Retourne None pour ne pas planter handle_photo

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("--- Entrée dans handle_photo ---")
    assistant = "INCONNU"
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌"
    donnees_extraites_ok = False
    reply_message_exists_for_error_handling = False
    topic_name_for_error_handling = ""
    username = "Non trouvé" # Initialisation
    reseau = "instagram" # Initialisation par défaut
    abonnés = None # Initialisation

    try:
        message = update.message
        if not message or not message.photo:
            logger.info("handle_photo: Message None ou sans photo, sortie.")
            return

        reply = message.reply_to_message
        if not reply or not hasattr(reply, "forum_topic_created") or not reply.forum_topic_created:
            logger.info("handle_photo: Pas une réponse à un topic valide, sortie.")
            return
        
        reply_message_exists_for_error_handling = True
        topic_name = reply.forum_topic_created.name
        topic_name_for_error_handling = topic_name

        if not topic_name.startswith("SUIVI "):
            logger.info(f"handle_photo: Nom du topic 	'{topic_name}	' non conforme, sortie.")
            return
        
        assistant = topic_name.replace("SUIVI ", "").strip().upper()
        logger.info(f"handle_photo: Assistant extrait: 	'{assistant}	'")
        # Le message par défaut est mis à jour avec l_assistant
        message_status_general = f"🤖 {today} - {assistant} - ❌ Analyse OCR impossible ❌"

        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        img_bytes_io = io.BytesIO()
        await file.download_to_memory(img_bytes_io)
        img_bytes_io.seek(0)
        img_content = img_bytes_io.read()

        image = Image.open(io.BytesIO(img_content))
        width, height = image.size
        # Le recadrage est conservé car il cible la zone d_intérêt
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
            # Pas besoin de raise ici, le message d_erreur sera envoyé dans le finally
            return # Sortie anticipée

        ocr_text_full = texts_annotations_vision[0].description if texts_annotations_vision and len(texts_annotations_vision) > 0 else ""
        logger.info(f"🔍 OCR Google Vision brut (premiers 500 caractères):\n{ocr_text_full[:500]}")

        if not ocr_text_full:
            logger.warning("handle_photo: OCR n_a retourné aucun texte.")
            return # Sortie anticipée

        # Identification du réseau
        if "getallmylinks.com" in ocr_text_full.lower(): reseau = "instagram"
        elif "beacons.ai" in ocr_text_full.lower(): reseau = "twitter"
        elif "tiktok" in ocr_text_full.lower() or any(k in ocr_text_full.lower() for k in ["followers", "j_aime", "abonnés", "abonné", "fans", "suivis"]):
            reseau = "tiktok"
        elif "threads" in ocr_text_full.lower(): reseau = "threads"
        elif any(x in ocr_text_full.lower() for x in ["modifier le profil", "suivi(e)s", "publications"]):
            reseau = "instagram"
        else: 
            reseau = "instagram" # Défaut si rien n_est clair
            logger.info("Réseau non clairement identifié, par défaut Instagram.")
        logger.info(f"handle_photo: Réseau identifié: {reseau}")

        # Extraction de l_username
        usernames_found = re.findall(r"@([a-zA-Z0-9_.-]{3,})", ocr_text_full)
        reseau_handles = KNOWN_HANDLES.get(reseau.lower(), [])
        username = "Non trouvé" # Réinitialisation pour cette extraction
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
                if username == "Non trouvé": username = u_from_url # Prend le premier URL si pas de match connu
        username = corriger_username(username, reseau)
        logger.info(f"🕵️ Username final : 	'{username}	' (réseau : {reseau})")

        # Extraction des abonnés
        abonnés = None # Réinitialisation
        if reseau == "tiktok":
            abonnés = extraire_followers_tiktok(texts_annotations_vision)
        else: # Logique pour Instagram, Twitter, Threads etc.
            match_explicit = re.search(r"(\d[\d.,\s]*[kKmM]?)\s*(?:abonnés|followers|suivies|suivi\(e\)s|abonné)", ocr_text_full, re.IGNORECASE)
            if match_explicit:
                abonnés = normaliser_nombre_followers(match_explicit.group(1))
            if not abonnés: # Fallback si pas de mot-clé explicite
                numbers_extracted_int = []
                raw_numbers = re.findall(r"(\d[\d.,\s]*[kKmM]?)", ocr_text_full)
                for num_str in raw_numbers:
                    val_norm = normaliser_nombre_followers(num_str)
                    if val_norm: 
                        # Exclure les nombres qui ressemblent à des heures
                        if not re.fullmatch(r"\d{1,2}:\d{2}", num_str.strip()):
                            numbers_extracted_int.append(int(val_norm))
                
                if len(numbers_extracted_int) >= 3: # Typiquement Suivis / Abonnés / J_aime
                    abonnés = str(numbers_extracted_int[1]) # Prend le deuxième
                elif len(numbers_extracted_int) == 2 and reseau == "instagram": # Peut être Abonnés / Publications
                    abonnés = str(numbers_extracted_int[0]) # Prend le premier (souvent abonnés)
                elif len(numbers_extracted_int) == 1 and reseau == "instagram": # Un seul nombre, on le prend
                    abonnés = str(numbers_extracted_int[0])
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
        logger.error(traceback.format_exc()) # Log du traceback complet
        assistant_nom_erreur = assistant # Utilise l_assistant déjà extrait si possible
        if assistant == "INCONNU" and reply_message_exists_for_error_handling and topic_name_for_error_handling.startswith("SUIVI "):
            assistant_nom_erreur = topic_name_for_error_handling.replace("SUIVI ", "").strip().upper()
        message_status_general = f"🤖 {today} - {assistant_nom_erreur} - ❌ Analyse OCR impossible ❌"

    finally:
        logger.info(f"Message à envoyer au General: {message_status_general}")
        try:
            await bot.send_message(chat_id=GROUP_ID, text=message_status_general)
            logger.info("Message de statut envoyé au sujet General.")
        except Exception as e_send_general:
            logger.error(f"Impossible d_envoyer le message de statut au sujet General: {e_send_general}")
            logger.error(traceback.format_exc())
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
        # S_assurer que application est disponible pour ContextTypes ou passer None
        context = ContextTypes.DEFAULT_TYPE(application=None, chat_id=update.effective_chat.id if update.effective_chat else None, user_id=update.effective_user.id if update.effective_user else None)
        await handle_photo(update, context)
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
        # Pour le mode polling, il faut une instance d_Application
        application = Application.builder().token(TOKEN).build()
        application.add_handler(MessageHandler(filters.PHOTO & (~filters.COMMAND), handle_photo))
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        logger.info("Lancement en mode webhook avec Uvicorn (localement)...")
        port = int(os.getenv("PORT", 8000))
        uvicorn.run(app, host="0.0.0.0", port=port)

