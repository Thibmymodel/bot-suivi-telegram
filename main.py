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
    nombre_str_test = nombre_str.replace(" ", "").strip()
    if not re.match(r"^[\d.,]*[kKmM]?$", nombre_str_test, re.IGNORECASE):
        logger.debug(f"normaliser_nombre_followers: L_entrée 	\"{nombre_str}\" (nettoyée en 	\"{nombre_str_test}\") ne correspond pas au format attendu.")
        return None
    nombre_str_clean = nombre_str_test.lower()
    valeur = None
    try:
        if "k" in nombre_str_clean:
            num_part = nombre_str_clean.replace("k", "").replace(",", ".")
            if not re.match(r"^\d*\.?\d+$", num_part):
                logger.debug(f"normaliser_nombre_followers: Format k invalide pour 	\"{nombre_str_clean}\" (partie numérique: 	\"{num_part}\")")
                return None
            valeur = str(int(float(num_part) * 1000))
        elif "m" in nombre_str_clean:
            num_part = nombre_str_clean.replace("m", "").replace(",", ".")
            if not re.match(r"^\d*\.?\d+$", num_part):
                logger.debug(f"normaliser_nombre_followers: Format m invalide pour 	\"{nombre_str_clean}\" (partie numérique: 	\"{num_part}\")")
                return None
            valeur = str(int(float(num_part) * 1000000))
        else:
            nombre_final_digits = re.sub(r"[^\d]", "", nombre_str_clean)
            if not nombre_final_digits.isdigit():
                logger.debug(f"normaliser_nombre_followers: 	\"{nombre_final_digits}\" (venant de 	\"{nombre_str_clean}\") n_est pas un digit après nettoyage final.")
                return None
            valeur = str(int(nombre_final_digits))
    except ValueError as e:
        logger.warning(f"normaliser_nombre_followers: ValueError lors de la conversion de 	\"{nombre_str_clean}\" (original: 	\"{nombre_str}\"): {e}")
        return None
    return valeur

def _fusionner_annotations_numeriques_adjacentes_instagram(number_annotations_list: list, reseau_nom: str) -> list:
    if reseau_nom != "instagram":
        return number_annotations_list

    logger.info(f"_fusionner_annotations_numeriques_adjacentes_instagram: Tentative de fusion pour {len(number_annotations_list)} annotations.")

    fragments = []
    others = []
    for ann in number_annotations_list:
        text = ann.get("text", "").strip()
        # Est un fragment numérique pur s_il ne contient que des chiffres, espaces, points, virgules
        # ET ne contient pas de ':' (heure), 'k', 'm', 's' (pour éviter de fusionner "10" avec "k followers")
        if re.fullmatch(r"[\d\s.,]+", text) and not any(c in text.lower() for c in [":", "k", "m", "s"]):
            fragments.append(ann)
        else:
            others.append(ann)

    if len(fragments) < 2:
        logger.info("_fusionner_annotations_numeriques_adjacentes_instagram: Pas assez de fragments (<2) ou aucun fragment trouvé pour tenter une fusion.")
        return number_annotations_list # Retourne la liste originale (fragments + others)

    fragments.sort(key=lambda ann: (ann["avg_y"], ann["avg_x"]))
    
    merged_fragments_successfully = []
    used_fragment_indices = [False] * len(fragments)

    for i in range(len(fragments)):
        if used_fragment_indices[i]:
            continue

        current_group_anns = [fragments[i]]
        current_group_texts = [fragments[i]["text"].strip()]
        used_fragment_indices[i] = True # Marquer comme utilisé pour ce groupe potentiel

        for j in range(i + 1, len(fragments)):
            if used_fragment_indices[j]: # Ne devrait pas arriver si la logique est correcte
                continue

            last_ann_in_group = current_group_anns[-1]
            next_ann = fragments[j]

            y_diff = abs(last_ann_in_group["avg_y"] - next_ann["avg_y"])
            x_diff_centers = next_ann["avg_x"] - last_ann_in_group["avg_x"]

            Y_LINE_THRESHOLD = 20  
            X_SPACE_THRESHOLD_MIN = 1 # Doit être à droite
            X_SPACE_THRESHOLD_MAX = 85 # Augmenté pour plus de flexibilité

            if y_diff < Y_LINE_THRESHOLD and X_SPACE_THRESHOLD_MIN < x_diff_centers < X_SPACE_THRESHOLD_MAX:
                current_group_texts.append(next_ann["text"].strip())
                current_group_anns.append(next_ann)
                used_fragment_indices[j] = True # Marquer comme utilisé dans ce groupe
            elif x_diff_centers >= X_SPACE_THRESHOLD_MAX or x_diff_centers <= 0: # Trop loin ou à gauche/superposé
                # Remettre used_fragment_indices[j] à False s_il avait été mis à True par erreur ? Non, car on break.
                break # Arrêter d_étendre ce groupe si le suivant est trop loin ou mal placé
        
        merged_text_with_spaces = " ".join(current_group_texts)
        normalized_value = normaliser_nombre_followers(merged_text_with_spaces)

        if normalized_value:
            base_ann = current_group_anns[0]
            merged_fragments_successfully.append({
                "text": merged_text_with_spaces, 
                "normalized": normalized_value,
                "avg_y": base_ann["avg_y"],
                "avg_x": base_ann["avg_x"],
                "annotation": base_ann["annotation"]
            })
            logger.info(f"_fusionner_annotations_numeriques_adjacentes_instagram: Fusionné {current_group_texts} -> '{merged_text_with_spaces}' (normalisé: {normalized_value})")
        else: # La fusion n_a pas donné un nombre valide, remettre les fragments originaux du groupe actuel
            for k_idx, ann_in_failed_group in enumerate(current_group_anns):
                 # Remettre l_index à False pour qu_il soit traité individuellement s_il n_était pas le premier du groupe
                original_fragment_index_in_fragments_list = fragments.index(ann_in_failed_group) # Peut être coûteux
                # Pour éviter la complexité de retrouver l_index, on les ajoute simplement
                # s_ils sont normalisables seuls.
                original_norm = normaliser_nombre_followers(ann_in_failed_group["text"])
                if original_norm:
                    merged_fragments_successfully.append({ 
                        "text": ann_in_failed_group["text"],
                        "normalized": original_norm,
                        "avg_y": ann_in_failed_group["avg_y"],
                        "avg_x": ann_in_failed_group["avg_x"],
                        "annotation": ann_in_failed_group["annotation"]
                    })
                # Et marquer l_index comme non-utilisé pour qu_il puisse être repris s_il n_est pas le premier
                if k_idx > 0 : # Si ce n_est pas le premier du groupe qui a échoué
                    # Ceci est compliqué. Plus simple: si la fusion échoue, on ne fait rien ici, et les used_fragment_indices
                    # pour les éléments après i (ceux de current_group_anns) seront toujours False s_ils n_ont pas été traités.
                    # La logique actuelle est : si on commence un groupe avec fragments[i], on le marque used.
                    # Si la fusion échoue, fragments[i] (et les autres du groupe) doivent être remis. C_est ce que fait la boucle externe.
                    pass
    
    # Ajouter les fragments qui n_ont PAS été utilisés dans une fusion réussie
    # (ceux pour lesquels used_fragment_indices est resté False ou ceux dont le groupe a échoué à la normalisation)
    # C_est déjà un peu géré ci-dessus par la remise des fragments en cas d_échec de normalisation du groupe.
    # Pour être sûr, on parcourt tous les fragments initiaux.
    final_processed_fragments = []
    temp_used_in_success = [False] * len(fragments)

    for merged_ann in merged_fragments_successfully:
        # Si merged_ann vient d_une fusion réelle (plus d_un texte original)
        original_texts_in_merge = merged_ann["text"].split(" ")
        if len(original_texts_in_merge) > 1 and merged_ann["text"] != merged_ann["annotation"].description.strip(): # Heuristique pour détecter une vraie fusion
            # Trouver les fragments originaux correspondants et les marquer
            # C_est trop complexe ici. On se fie à ce que merged_fragments_successfully contienne le bon set.
            pass
        final_processed_fragments.append(merged_ann)
    
    # Quels fragments n_ont pas été inclus dans `merged_fragments_successfully` ?
    # On reconstruit la liste des fragments traités.
    # `merged_fragments_successfully` contient soit des groupes fusionnés, soit des fragments individuels (si leur groupe a échoué à la normalisation).
    # On doit s_assurer de ne pas avoir de doublons et que tous les fragments originaux y sont représentés une fois.

    # Simplification: `merged_fragments_successfully` est la liste des fragments après tentative de fusion.
    # Elle devrait contenir tous les éléments de `fragments`, soit fusionnés, soit tels quels.
    # On la combine avec `others`.

    final_number_annotations = merged_fragments_successfully + others
    
    if len(final_number_annotations) != len(number_annotations_list):
        logger.info(f"_fusionner_annotations_numeriques_adjacentes_instagram: Taille de la liste modifiée. Avant: {len(number_annotations_list)}, Après: {len(final_number_annotations)}")
    else:
        logger.info("_fusionner_annotations_numeriques_adjacentes_instagram: Taille de la liste inchangée après tentative de fusion.")

    return final_number_annotations

def extraire_followers_spatial(text_annotations, mots_cles_specifiques, reseau_nom="inconnu") -> str | None:
    try:
        logger.info(f"extraire_followers_spatial ({reseau_nom}): --- Début de l_extraction spatiale ---")
        keyword_annotations_list = []
        number_annotations_list = []

        if not text_annotations:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucune annotation de texte fournie.")
            return None
        
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre total d_annotations reçues: {len(text_annotations)}")
        # ... (logging des premières annotations)

        for i, annotation in enumerate(text_annotations[1:]):
            try:
                if not hasattr(annotation, 'description') or not hasattr(annotation, 'bounding_poly'):
                    continue
                text = annotation.description.lower().strip()
                if not hasattr(annotation.bounding_poly, 'vertices') or len(annotation.bounding_poly.vertices) < 4:
                    continue
                vertices = annotation.bounding_poly.vertices
                avg_y = (vertices[0].y + vertices[1].y + vertices[2].y + vertices[3].y) / 4
                avg_x = (vertices[0].x + vertices[1].x + vertices[2].x + vertices[3].x) / 4

                if any(keyword.lower() in text for keyword in mots_cles_specifiques):
                    keyword_annotations_list.append({"text": text, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
                
                # Regex pour identifier les nombres potentiels (y compris avec k/m, espaces, points, virgules)
                # Exclut les formats d_heure simples comme "10:30"
                if re.search(r"\d", text) and re.match(r"^[\d.,\s]*[kKmMsS]?$", text, re.IGNORECASE) and not re.fullmatch(r"\d{1,2}:\d{2}[\s\w]*", text, re.IGNORECASE):
                    nombre_normalise_test = normaliser_nombre_followers(text)
                    if nombre_normalise_test:
                        number_annotations_list.append({"text": text, "normalized": nombre_normalise_test, "avg_y": avg_y, "avg_x": avg_x, "annotation": annotation})
            except Exception as e_loop_ann:
                logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR INATTENDUE lors du traitement de l_annotation {i}: {e_loop_ann}")
                continue 

        logger.info(f"extraire_followers_spatial ({reseau_nom}): Fin de la boucle d_analyse des annotations.")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de mots-clés trouvés: {len(keyword_annotations_list)}")
        logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de nombres potentiels trouvés AVANT fusion: {len(number_annotations_list)}")

        if reseau_nom == "instagram":
            number_annotations_list = _fusionner_annotations_numeriques_adjacentes_instagram(number_annotations_list, reseau_nom)
            logger.info(f"extraire_followers_spatial ({reseau_nom}): Nombre de nombres potentiels trouvés APRES fusion: {len(number_annotations_list)}")

        for idx, na in enumerate(number_annotations_list):
            logger.info(f"  - Nombre {idx} (post-fusion): {na['text']} (normalisé: {na['normalized']}) à y={na['avg_y']}")

        if not keyword_annotations_list:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun mot-clé de followers trouvé. Tentative de fallback.")
            if len(number_annotations_list) >= 3:
                number_annotations_list.sort(key=lambda ann: ann['avg_x'])
                if (abs(number_annotations_list[0]['avg_y'] - number_annotations_list[1]['avg_y']) < 30 and 
                    abs(number_annotations_list[1]['avg_y'] - number_annotations_list[2]['avg_y']) < 30):
                    return number_annotations_list[1]['normalized']
            return None

        best_candidate = None
        min_distance = float('inf')

        for kw_ann in keyword_annotations_list:
            for num_ann in number_annotations_list:
                y_diff = num_ann['avg_y'] - kw_ann['avg_y'] 
                x_diff = abs(kw_ann['avg_x'] - num_ann['avg_x'])
                if y_diff > -25 and y_diff < 100 and x_diff < 150: 
                    distance = (y_diff**2 + x_diff**2)**0.5
                    if distance < min_distance:
                        try:
                            min_distance = distance
                            best_candidate = num_ann['normalized']
                        except ValueError:
                            pass # Ignorer si la normalisation a échoué plus tôt
        
        if best_candidate:
            return best_candidate
        else:
            logger.warning(f"extraire_followers_spatial ({reseau_nom}): Aucun candidat sélectionné. Fallback sur le plus grand nombre.")
            if number_annotations_list:
                number_annotations_list.sort(key=lambda x: int(x.get("normalized", "0") or "0"), reverse=True)
                if number_annotations_list and number_annotations_list[0]['normalized']:
                     return number_annotations_list[0]['normalized']
            return None

    except Exception as e_global_spatial:
        logger.error(f"extraire_followers_spatial ({reseau_nom}): ERREUR GLOBALE INATTENDUE: {e_global_spatial}")
        logger.error(traceback.format_exc())
        return None

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.message.from_user
        file_id = update.message.photo[-1].file_id
        logger.info(f"Photo reçue de {user.username} (ID: {user.id}), file_id: {file_id}")

        if file_id in already_processed:
            logger.info(f"Image {file_id} déjà traitée. Ignorée.")
            # await update.message.reply_text("Cette image a déjà été traitée.") # Optionnel
            return
        
        new_file = await context.bot.get_file(file_id)
        file_path = await new_file.download_to_drive()
        logger.info(f"Photo téléchargée: {file_path}")

        with io.open(file_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations

        if response.error.message:
            raise Exception(f"Erreur de l_API Vision: {response.error.message}")

        if not texts:
            logger.warning("Aucun texte détecté dans l_image.")
            await update.message.reply_text("Aucun texte n_a été détecté dans l_image.")
            return

        reseau_nom, username, followers = identifier_reseau_et_username_par_ocr(texts, KNOWN_HANDLES)
        
        if username:
            username = corriger_username(username, reseau_nom.lower() if reseau_nom else "inconnu")
            logger.info(f"Réseau identifié: {reseau_nom}, Utilisateur: {username}, Followers: {followers}")
            
            # Message de confirmation dans le groupe Telegram
            message_confirmation = f"🤖 {datetime.datetime.now().strftime('%d/%m/%Y')} - {username.upper()} - "
            if followers:
                message_confirmation += f"{followers} followers"
                # Écrire dans Google Sheets
                try:
                    row = [datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username, followers, reseau_nom, user.username, file_id]
                    sheet.append_row(row)
                    logger.info(f"Données écrites dans Google Sheets pour {username}")
                except Exception as e_gsheet:
                    logger.error(f"Erreur lors de l_écriture dans Google Sheets: {e_gsheet}")
                    logger.error(traceback.format_exc())
                    await bot.send_message(GROUP_ID, text=f"⚠️ Erreur lors de l_écriture GSheet pour {username}: {e_gsheet}")
            else:
                message_confirmation += "❌ Analyse OCR followers impossible ❌"
            
            await bot.send_message(GROUP_ID, text=message_confirmation)
            already_processed.add(file_id) # Ajouter après traitement réussi
        else:
            logger.warning("Impossible d_identifier le réseau ou l_utilisateur.")
            await bot.send_message(GROUP_ID, text=f"🤖 {datetime.datetime.now().strftime('%d/%m/%Y')} - ❓ Compte inconnu - ❌ Analyse OCR impossible (réseau/user non identifié) ❌")
            already_processed.add(file_id) # Ajouter même si échec pour éviter re-traitement

    except Exception as e:
        logger.error(f"Erreur dans handle_photo: {e}")
        logger.error(traceback.format_exc())
        await bot.send_message(GROUP_ID, text=f"🤖 Erreur critique dans le bot: {e}. Consultez les logs.")
    finally:
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Fichier temporaire {file_path} supprimé.")
            except OSError as e_remove:
                logger.error(f"Erreur lors de la suppression du fichier temporaire {file_path}: {e_remove}")

def identifier_reseau_et_username_par_ocr(text_annotations, known_handles):
    full_text_ocr = text_annotations[0].description.lower() if text_annotations else ""
    logger.info(f"Texte OCR complet pour identification: {full_text_ocr[:500]}...")

    # Identification du réseau et de l_username
    reseau_nom = "Inconnu"
    username_ocr = None
    followers = None

    # Keywords pour chaque réseau
    mots_cles = {
        "instagram": ["profil", "publications", "followers", "suivi(e)s", "modifier profil", "voir les traductions"],
        "twitter": ["profil", "abonnements", "abonnés", "tweets", "éditer le profil"],
        "threads": ["profil", "followers", "threads", "réponses", "republications"],
        "tiktok": ["profil", "abonnements", "followers", "j_aime", "modifier le profil", "partager le profil"]
    }
    mots_cles_followers_specifiques = {
        "instagram": ["followers", "abonnés"], # Le nombre est au-dessus
        "twitter": ["abonnés", "followers"],    # Le nombre est à gauche
        "threads": ["followers"],            # Le nombre est à gauche
        "tiktok": ["followers", "abonnés"]     # Le nombre est au-dessus
    }

    # 1. Essayer d_identifier le réseau via les known_handles (plus fiable)
    for handle_type, handles_list in known_handles.items():
        for handle_info in handles_list:
            handle = handle_info["id"]
            # Vérifier si le handle (avec ou sans @) est dans le texte OCR
            if f"@{handle.lower()}" in full_text_ocr or handle.lower() in full_text_ocr:
                reseau_nom = handle_info.get("reseau", handle_type.capitalize())
                username_ocr = handle
                logger.info(f"Correspondance trouvée via known_handles: Réseau='{reseau_nom}', User='{username_ocr}'")
                break
        if username_ocr:
            break
    
    # 2. Si non trouvé par known_handles, essayer par mots-clés génériques du réseau
    if not username_ocr:
        logger.info("Aucune correspondance via known_handles. Tentative par mots-clés génériques.")
        best_match_reseau = None
        max_keyword_count = 0
        for net, keywords in mots_cles.items():
            count = sum(1 for kw in keywords if kw in full_text_ocr)
            if count > max_keyword_count:
                max_keyword_count = count
                best_match_reseau = net
        
        if best_match_reseau and max_keyword_count > 1: # Nécessite au moins 2 mots-clés pour réduire les faux positifs
            reseau_nom = best_match_reseau.capitalize()
            logger.info(f"Réseau identifié par mots-clés génériques: '{reseau_nom}' (count: {max_keyword_count})")
            # Essayer d_extraire un @username si possible pour ce réseau
            match_username = re.search(r"@([a-zA-Z0-9_.]+)", full_text_ocr)
            if match_username:
                username_ocr = match_username.group(1)
                logger.info(f"Username extrait par regex après identification réseau: '{username_ocr}'")
            else: # Si pas de @username, chercher un nom probable près des mots-clés (plus complexe, pour plus tard)
                logger.warning(f"Réseau '{reseau_nom}' identifié, mais pas de @username trouvé par regex.")
        else:
            logger.warning("Identification du réseau par mots-clés génériques incertaine ou échouée.")

    # 3. Extraction des followers si réseau identifié (même si username pas parfait)
    if reseau_nom != "Inconnu":
        current_mots_cles_followers = mots_cles_followers_specifiques.get(reseau_nom.lower(), [])
        if not current_mots_cles_followers:
            logger.warning(f"Pas de mots-clés followers spécifiques pour le réseau {reseau_nom}")
        else:
            logger.info(f"Utilisation des mots-clés followers pour {reseau_nom}: {current_mots_cles_followers}")
            followers = extraire_followers_spatial(text_annotations, current_mots_cles_followers, reseau_nom.lower())
            if followers:
                logger.info(f"Followers extraits pour {reseau_nom}: {followers}")
            else:
                logger.warning(f"Échec de l_extraction des followers pour {reseau_nom} avec la méthode spatiale.")
    else:
        logger.warning("Réseau non identifié, impossible d_extraire les followers.")

    # Si username_ocr n_est toujours pas trouvé mais réseau oui, on peut tenter un fallback plus tard
    if not username_ocr and reseau_nom != "Inconnu":
        logger.warning(f"Réseau {reseau_nom} identifié, mais username_ocr est None. OCR complet utilisé comme fallback pour nom de compte.")
        # On pourrait essayer de prendre le premier mot proéminent comme username, mais risqué.
        # Pour l_instant, on ne met rien si pas de @user ou de known_handle.

    return reseau_nom, username_ocr, followers

def main() -> None:
    logger.info("Démarrage du bot...")
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.GROUPS, handle_photo))
    logger.info("Gestionnaire de photos ajouté.")
    application.run_polling()

if __name__ == "__main__":
    main()

