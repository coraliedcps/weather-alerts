#!/bin/sh
# Mise à jour automatique sur le Pi (lancée par un timer systemd) : avance sur origin/main,
# reconstruit l'image, redéploie, et revient à l'image précédente si le conteneur ne démarre pas.
#   tools/auto_update.sh [--force]   # --force : reconstruit même sans nouveau commit
# ALERT_TOPIC_FILE (optionnel) : fichier local contenant un topic ntfy.sh pour signaler les échecs.
set -eu
cd "$(dirname "$0")/.."
IMAGE=weather-alerts:local
CONTAINER=weather-alerts

notify() {
    [ -n "${ALERT_TOPIC_FILE:-}" ] && [ -r "$ALERT_TOPIC_FILE" ] || return 0
    curl -s -o /dev/null -H "X-Title: weather-alerts — mise à jour" -H "X-Tags: warning" \
        --data-binary "$1" "https://ntfy.sh/$(cat "$ALERT_TOPIC_FILE")" || true
}
fail() {
    echo "$1" >&2
    notify "$1"
    exit 1
}

git fetch -q origin main || fail "git fetch a échoué"
before=$(git rev-parse HEAD)
git merge -q --ff-only origin/main || fail "fast-forward impossible (commits ou modifs locales ?)"
after=$(git rev-parse HEAD)
if [ "$before" = "$after" ] && [ "${1:-}" != "--force" ]; then
    echo "déjà à jour ($after)"
    exit 0
fi
echo "mise à jour $before → $after"

docker image tag "$IMAGE" weather-alerts:previous 2>/dev/null || true
docker build -q --network host -t "$IMAGE" . || fail "build échoué sur $after"
docker compose up -d --no-build || fail "docker compose up a échoué sur $after"

# Le conteneur doit être en marche (pas en boucle de redémarrage) après 60 s.
sleep 60
running=$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$CONTAINER" 2>/dev/null || echo "false 0")
[ "$running" = "true 0" ] && { echo "déployé ($after)"; exit 0; }

docker image tag weather-alerts:previous "$IMAGE" && docker compose up -d --no-build
fail "déploiement de $after en échec ($running) : image précédente restaurée"
