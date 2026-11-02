# weather-alerts

Alertes **vent (rafales)** et **cyclones** pour un ou plusieurs emplacements, envoyées en notification push sur votre téléphone via [ntfy](https://ntfy.sh). Tout tourne gratuitement sur GitHub Actions, toutes les heures. Chaque notification porte en titre « Weather Alerts — … ».

## Ce que ça fait

- **Vent** : alerte si les rafales prévues dans les **5 prochains jours** atteignent ou dépassent le seuil de l'emplacement (35 kts par défaut). Source : Open-Meteo.
- **Cyclones** : alerte si un cyclone actif, ou sa trajectoire prévue, passe à moins de `rayon_cyclone_km`. Source : GDACS.
- **Anti-spam** : une alerte n'est envoyée qu'une fois. Nouvelle notification seulement si ça s'aggrave (rafales +5 kts, cyclone 50 km plus près, catégorie supérieure, passage en urgence) ou si l'alerte se termine.
- **Contrôle quotidien** : une notification discrète « Surveillance OK — RAS » par jour (8 h, heure de Manille). Si elle n'arrive pas, le système est en panne.
- **Panne de source** : si une source de données est injoignable pendant ~3 h, vous êtes prévenue.

| Situation | Priorité ntfy |
|---|---|
| Rafales ≥ seuil | Haute |
| Cyclone dans le rayon | Haute |
| Cyclone à moins de 200 km | Urgente (sonne même en silencieux) |
| Fin d'alerte, source en panne | Normale |
| Contrôle quotidien | Basse |

## Mise en route pas à pas

### 1. Installer ntfy sur le téléphone

1. Installez l'appli **ntfy** (Android : Play Store ou F-Droid ; iPhone : App Store).
2. Choisissez un nom de canal long et imprévisible, par exemple `bateau-alerte-x7k2q9` (inventez le vôtre : quiconque connaît le nom peut lire vos alertes).
3. Dans l'appli : **+** → saisissez ce nom → **S'abonner**.
4. Pour que la priorité *urgente* sonne même en mode silencieux : réglages de l'appli, canal de notification « Urgent » → autoriser « Ne pas déranger ».

### 2. Créer le dépôt GitHub (privé)

1. https://github.com/new → nom `weather-alerts` → **Private** (vos coordonnées ne doivent pas être publiques).
2. Envoyez-y le contenu de ce dossier (`weather_alerts.py`, `requirements.txt`, `config.example.yaml`, `.github/`, `tests/`, `.gitignore`, `README.md`), à la racine du dépôt. `.github/workflows/check.yml` doit être à la racine, sinon GitHub ne le voit pas.

### 3. Configurer

1. Copiez `config.example.yaml` en **`config.yaml`**.
2. Mettez votre nom de canal dans `ntfy_topic` et vos emplacements (nom, lat, lon, seuil, rayon).
   - Variante plus discrète : laissez `CHANGEZ-MOI` dans le fichier et créez un secret `NTFY_TOPIC` (Settings → Secrets and variables → Actions → New repository secret). Le secret prend le dessus.
3. Ajouter un emplacement = ajouter 5 lignes au fichier.

### 4. Tester avant d'activer

Sur votre ordinateur (Python 3.10+) :

```bash
pip install -r requirements.txt
python weather_alerts.py --test       # doit faire sonner votre téléphone
python weather_alerts.py --dry-run    # affiche ce qui serait envoyé, sans rien envoyer
```

### 5. Activer sur GitHub

1. Poussez `config.yaml` sur le dépôt.
2. Onglet **Actions** → si GitHub le demande, activez les workflows.
3. Ouvrez **Surveillance météo** → **Run workflow** pour un premier passage immédiat.
4. Vérifiez que le job est vert et que `state.json` apparaît dans le dépôt.

Ensuite tout est automatique.

## Limites à connaître

- **Pas de temps réel à la seconde** : GitHub retarde parfois les tâches planifiées de 5 à 15 min. Les bulletins cycloniques sortent toutes les 6 h (3 h près des terres) ; vous êtes prévenue au passage suivant (au plus ~1 h après).
- **Dépôt inactif** : GitHub désactive les tâches planifiées après 60 jours sans activité. Le contrôle quotidien modifie `state.json` chaque jour, ce qui crée un commit et garde le dépôt actif.
- **Minutes gratuites** : ~24 passages/jour ≈ 750 min/mois, sous les 2 000 du plan gratuit (dépôt privé).
- **Cyclones (GDACS)** : GDACS ne liste que les systèmes jugés significatifs ; une toute petite dépression peut manquer. Le format de sa trajectoire n'a pas pu être vérifié en direct lors de l'écriture (réseau bloqué) : au premier passage, regardez les journaux du job et lancez `--dry-run` quand un cyclone est actif pour confirmer que la distance affichée est cohérente.
- **Rafales** : Open-Meteo donne la rafale maximale de chaque heure ; les rafales locales réelles (effet de côte, de relief) peuvent dépasser la prévision.

## Réglages facultatifs

Voir le bas de `config.example.yaml` (horizon, rayon d'urgence, heure du contrôle quotidien, seuils d'aggravation).

## Version 2 (hors MVP)

Signaux PAGASA par province, seuil sur le vent moyen, petite page web pour éditer les emplacements.

## Développement

```bash
pip install pytest -r requirements.txt
python -m pytest tests
```
