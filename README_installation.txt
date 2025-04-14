📦 INSTALLATION DU BOT TELEGRAM OCR ➜ GOOGLE SHEETS

Ce bot permet d’analyser des captures d’écran postées dans un groupe Telegram, d’en extraire les comptes et abonnés via OCR, et de consigner automatiquement les données dans un Google Sheet.

---

✅ PRÉREQUIS

1. Python 3.10+
2. Tesseract installé localement (pour test local uniquement)
3. Un compte Render connecté à GitHub
4. Un bot Telegram (token)
5. Un Google Sheet avec un onglet nommé "Données Journalières"

---

⚙️ FICHIERS À FOURNIR (dans Render uniquement, pas dans GitHub)

1. **TELEGRAM_BOT_TOKEN** → variable d’environnement
2. **TELEGRAM_GROUP_ID** → variable d’environnement (ex. : -100231xxxx)
3. **SPREADSHEET_ID** → variable d’environnement (ID du Google Sheet)
4. **GOOGLE_APPLICATION_CREDENTIALS_JSON** → variable contenant le contenu du `credentials.json` converti en ligne (voir `format_credentials.py`)

---

🚀 INSTALLATION EN LOCAL (pour test)

```bash
pip install -r requirements.txt
python main.py
