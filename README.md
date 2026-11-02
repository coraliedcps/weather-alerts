# weather-alerts

Alertes **vent (rafales)** et **cyclones** pour un ou plusieurs emplacements, envoyées en notification push sur votre téléphone via [ntfy](https://ntfy.sh). Tourne en continu sur un Raspberry Pi (Docker), comme `commute-alerts`, avec un passage toutes les heures. Chaque notification porte en titre « Weather Alerts — … ».

## Ce que ça fait

- **Vent** : alerte si les rafales prévues dans les **5 prochains jours** atteignent ou dépassent le seuil de l'emplacement (35 kts par défaut). Source : Open-Meteo, avec MET Norway (institut météo norvégien, gratuit, sans clé) en secours automatique si Open-Meteo ne répond pas. La notification précise alors « source de secours : MET Norway ».
- **Cyclones** : alerte si un cyclone actif, ou sa trajectoire prévue, passe à moins de `rayon_cyclone_km`. Source : GDACS.
- **Signaux PAGASA** : alerte quand un signal officiel d'alerte vent (TCWS n° 1 à 5) est en vigueur sur la province de l'emplacement. Source : page des bulletins cycloniques de PAGASA.
- **Anti-spam** : une alerte n'est envoyée qu'une fois. Nouvelle notification seulement si ça s'aggrave (rafales +5 kts, cyclone 50 km plus près, catégorie supérieure, passage en urgence) ou si l'alerte se termine.
- **Contrôle quotidien** : une notification discrète « Surveillance OK — RAS » par jour (8 h, heure de Manille). Si elle n'arrive pas, le système est en panne.
- **Panne de source** : si une source de données est injoignable pendant ~3 h, vous êtes prévenue.

| Situation | Priorité ntfy |
|---|---|
| Rafales ≥ seuil | Haute |
| Cyclone dans le rayon | Haute |
| Cyclone à moins de 200 km | Urgente (sonne même en silencieux) |
| Signal PAGASA n° 1 ou 2 | Haute |
| Signal PAGASA n° 3, 4 ou 5 | Urgente |
| Fin d'alerte, source en panne | Normale |
| Contrôle quotidien | Basse |

## Installation sur le Raspberry Pi

Même principe que `commute-alerts` : un conteneur Docker qui tourne en permanence et redémarre tout seul (coupure de courant, reboot).

Toutes les commandes ci-dessous se tapent **sur le Pi**, dans un terminal (en SSH par exemple). Copiez-collez-les une par une.

### 1. Récupérer le projet

Allez dans le dossier qui contient déjà `commute-alerts` (le dossier parent) :

```bash
cd ~
```

Regardez comment `commute-alerts` a été cloné, pour utiliser la même méthode :

```bash
git -C commute-alerts remote -v
```

- Si l'adresse commence par `git@github.com:` (clé SSH) :

  ```bash
  git clone git@github.com:coraliedcps/weather-alerts.git
  ```

- Si elle commence par `https://` :

  ```bash
  git clone https://github.com/coraliedcps/weather-alerts.git
  ```

  (GitHub demande alors votre identifiant et, comme mot de passe, un jeton d'accès personnel.)

Puis entrez dans le dossier :

```bash
cd weather-alerts
```

### 2. Régler ntfy : le fichier `.env`

Créez le fichier à partir du modèle :

```bash
cp .env.example .env
```

Le plus simple est de reprendre l'adresse du serveur et le jeton de `commute-alerts` :

```bash
grep -E '^NTFY_(URL|TOKEN)=' ../commute-alerts/.env
```

Ouvrez `.env` pour le modifier :

```bash
nano .env
```

- `NTFY_URL` et `NTFY_TOKEN` : recopiez les valeurs affichées juste avant.
- `NTFY_TOPIC` : mettez un **nouveau** nom de canal, différent de celui des alertes TER, pour séparer les deux types d'alertes dans l'appli.
  - ⚠️ Sur un serveur ntfy public (ntfy.sh), **le nom du canal est le seul secret** : n'importe qui qui le devine peut lire vos alertes (elles révèlent où vous êtes) ou y publier de fausses alertes. Évitez un nom évident comme `weather-alerts` ; ajoutez une suite aléatoire, comme pour le canal TER. Pour en générer un : `echo "weather-alerts-$(head -c 16 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"`
  - Si votre serveur ntfy a des droits par canal, autorisez ce nouveau canal pour le jeton.

Enregistrez avec `Ctrl+O` puis `Entrée`, quittez avec `Ctrl+X`.

Sur le téléphone, dans l'appli ntfy : **+** → choisissez votre serveur (le même que pour les alertes TER) → saisissez le nouveau nom de canal → **S'abonner**.

### 3. Régler les emplacements : le fichier `config.yaml`

```bash
cp config.example.yaml config.yaml
```

Il contient déjà Tambobo Bay et Dumaguete (35 kts, 500 km, province Negros Oriental). Pour modifier : `nano config.yaml`. Pour un emplacement hors des Philippines, ajoutez `fuseau` (ex. `"America/Adak"`) pour que les heures des messages soient en heure locale même avec la source de secours.

### 4. Construire et démarrer

```bash
docker build --network host -t weather-alerts:local .
```

```bash
docker compose run --rm weather-alerts --test
```

→ votre téléphone doit recevoir « Weather Alerts — Test ». Sinon, vérifiez `.env` (étape 2).

```bash
docker compose up -d
```

C'est parti : un passage maintenant, puis toutes les heures. Pour voir ce qui se passe :

```bash
docker compose logs -f
```

(`Ctrl+C` pour quitter l'affichage ; ça n'arrête pas la surveillance.) Vous devez voir `passage terminé` au bout d'une minute ou deux.

### 5. (Facultatif) Mises à jour automatiques

Comme pour `commute-alerts`, `tools/auto_update.sh` récupère les nouveautés publiées sur GitHub, reconstruit et redémarre (et revient à l'ancienne version si la nouvelle ne démarre pas). À lancer chaque jour par un timer systemd. Remplacez `VOTRE_UTILISATEUR` par votre nom d'utilisateur sur le Pi (commande `whoami`) :

```bash
sudo tee /etc/systemd/system/weather-alerts-update.service >/dev/null <<'UNIT'
[Service]
Type=oneshot
User=VOTRE_UTILISATEUR
ExecStart=/home/VOTRE_UTILISATEUR/weather-alerts/tools/auto_update.sh
UNIT
```

```bash
sudo tee /etc/systemd/system/weather-alerts-update.timer >/dev/null <<'UNIT'
[Timer]
OnCalendar=*-*-* 11:30
RandomizedDelaySec=30m
Persistent=true
[Install]
WantedBy=timers.target
UNIT
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now weather-alerts-update.timer
```

## Commandes utiles (dans le dossier `weather-alerts`)

| Pour… | Commande |
|---|---|
| Voir les journaux | `docker compose logs -f` |
| Notification de test | `docker compose run --rm weather-alerts --test` |
| Voir ce qui serait envoyé, sans rien envoyer | `docker compose run --rm weather-alerts --dry-run` |
| Voir les signaux PAGASA lus | `docker compose run --rm weather-alerts --pagasa` |
| État de santé | `docker compose ps` (colonne STATUS : `healthy`) |
| Appliquer une modif de `config.yaml` ou `.env` | `docker compose up -d --force-recreate` |
| Arrêter / relancer | `docker compose down` / `docker compose up -d` |

## Limites à connaître

- **Le Pi doit rester allumé et connecté.** S'il s'arrête, plus d'alertes : c'est le rôle du contrôle quotidien « Surveillance OK ». S'il ne vient pas, allez voir le Pi.
- **Contrôle quotidien** : réglé à 8 h heure de Manille, soit 1 h ou 2 h du matin en France ; en priorité basse il ne sonne pas. Pour le recevoir le matin en France : `fuseau: "Europe/Paris"` dans `config.yaml`.
- **Délai** : les bulletins cycloniques sortent toutes les 6 h (3 h près des terres) ; vous êtes prévenue au passage suivant, au plus ~1 h après (`intervalle_minutes` dans `config.yaml` pour aller plus vite).
- **Cyclones (GDACS)** : GDACS ne liste que les systèmes jugés significatifs ; une toute petite dépression peut manquer. Le format de sa trajectoire n'a pas pu être vérifié en direct lors de l'écriture (réseau bloqué) : au premier passage, regardez les journaux (`docker compose logs`) et lancez `--dry-run` quand un cyclone est actif pour confirmer que la distance affichée est cohérente.
- **Signaux PAGASA** : PAGASA ne fournit pas de données structurées ; le script lit la page web des bulletins et y cherche « Wind Signal No. X » suivi des provinces. Points à connaître :
  - Si un signal ne couvre qu'**une partie** de la province, vous êtes quand même alertée (prudence) ; la notification cite l'extrait du bulletin (« northern portion of Negros Oriental (Guihulngan City…) ») pour que vous jugiez.
  - Une baisse de signal n'est pas notifiée ; une hausse ou la levée complète l'est.
  - Ce lecteur n'a pas pu être testé sur la vraie page (réseau bloqué lors de l'écriture). Lors du prochain cyclone, lancez `docker compose run --rm weather-alerts --pagasa` et comparez avec le site de PAGASA. Si la page devient illisible (format changé, site en panne), vous recevez « Surveillance dégradée » après ~3 h.
- **Rafales** : Open-Meteo donne la rafale maximale de chaque heure ; les rafales locales réelles (effet de côte, de relief) peuvent dépasser la prévision.
- **Source de secours (MET Norway)** : prévision heure par heure sur ~2 jours et demi, puis toutes les 6 h ; au-delà, une rafale brève entre deux échéances peut passer inaperçue. Elle n'est utilisée que pendant les pannes d'Open-Meteo. Les journaux indiquent `fetch_open_meteo en échec` quand le secours prend le relais.

## Réglages facultatifs

Voir le bas de `config.example.yaml` (horizon, rayon d'urgence, heure du contrôle quotidien, seuils d'aggravation).

## Plus tard

Seuil sur le vent moyen, petite page web pour éditer les emplacements.

## Développement

```bash
pip install pytest -r requirements.txt
python -m pytest tests
python weather_alerts.py --dry-run   # hors Docker : lit config.yaml, NTFY_* depuis l'environnement
```
