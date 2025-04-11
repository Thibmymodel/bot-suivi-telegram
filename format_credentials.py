import json

# Charge le fichier credentials.json
with open("credentials.json", "r", encoding="utf-8") as f:
    creds = json.load(f)

# Convertit le JSON en une seule ligne de texte échappé
escaped = json.dumps(creds)

# Sauvegarde dans result.txt
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(escaped)

print("✅ Credentials formatés avec succès ! Contenu sauvegardé dans result.txt")
