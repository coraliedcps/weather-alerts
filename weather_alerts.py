#!/usr/bin/env python3
"""weather-alerts : surveille le vent (rafales) et les cyclones autour d'emplacements
et envoie des notifications push via ntfy. Prévu pour tourner toutes les 30 min
(GitHub Actions) ; l'état est gardé dans state.json pour ne pas spammer.

Sources (gratuites, sans clé) :
  - Open-Meteo : prévisions horaires de rafales et de vent moyen (en nœuds)
  - GDACS      : cyclones tropicaux actifs et trajectoire prévue
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import yaml

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
GDACS_EVENTS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
USER_AGENT = "weather-alerts/1.0"
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
FAILURES_BEFORE_WARNING = 6  # 6 passages de suite en échec (~3 h) -> on prévient

# Priorités ntfy
P_LOW, P_NORMAL, P_HIGH, P_URGENT = 2, 3, 4, 5

DEFAULTS = {
    "ntfy_server": "https://ntfy.sh",
    "fuseau": "Asia/Manila",       # fuseau pour l'heure du contrôle quotidien
    "heure_controle": 8,           # heure locale du « Surveillance OK »
    "horizon_heures": 120,         # 5 jours
    "rayon_urgence_km": 200,       # cyclone plus près que ça -> priorité urgente
    "marge_fin_kts": 2,            # hystérésis : fin d'alerte sous seuil - marge
    "escalade_rafales_kts": 5,     # re-notifier si les rafales gagnent X kts
    "escalade_cyclone_km": 50,     # re-notifier si le cyclone se rapproche de X km
}
LOC_DEFAULTS = {"seuil_rafales_kts": 35, "rayon_cyclone_km": 500}


# --------------------------------------------------------------------------- utilitaires
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def get_json(url: str, timeout: int = 30, retries: int = 3):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"échec de {url} : {last}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass_fr(deg: float) -> str:
    names = ["le nord", "le nord-est", "l'est", "le sud-est",
             "le sud", "le sud-ouest", "l'ouest", "le nord-ouest"]
    return names[int((deg + 22.5) // 45) % 8]


def densify(points: list[tuple[float, float]], step_km: float = 20.0) -> list[tuple[float, float]]:
    """Ajoute des points intermédiaires sur une ligne pour mesurer la distance au tracé."""
    out: list[tuple[float, float]] = []
    for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:]):
        n = max(1, int(haversine_km(a_lat, a_lon, b_lat, b_lon) // step_km))
        for i in range(n):
            f = i / n
            out.append((a_lat + (b_lat - a_lat) * f, a_lon + (b_lon - a_lon) * f))
    if points:
        out.append(points[-1])
    return out


def parse_time(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- configuration
def load_config(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise SystemExit(f"Config introuvable : {path} (copiez config.example.yaml)")
    cfg = {**DEFAULTS, **raw}
    topic = os.environ.get("NTFY_TOPIC", "").strip() or str(cfg.get("ntfy_topic") or "").strip()
    if not topic or topic == "CHANGEZ-MOI":
        raise SystemExit("ntfy_topic manquant (config.yaml ou secret NTFY_TOPIC)")
    cfg["ntfy_topic"] = topic
    locs = cfg.get("emplacements") or []
    if not locs:
        raise SystemExit("Aucun emplacement configuré")
    cfg["emplacements"] = []
    for loc in locs:
        merged = {**LOC_DEFAULTS, **loc}
        for key in ("nom", "lat", "lon"):
            if key not in merged:
                raise SystemExit(f"Emplacement invalide (champ « {key} » manquant) : {loc}")
        cfg["emplacements"].append(merged)
    return cfg


def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("alerts", {})
    state.setdefault("failures", {})
    return state


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- notifications
class Ntfy:
    def __init__(self, server: str, topic: str):
        self.server, self.topic = server.rstrip("/"), topic

    def send(self, title: str, message: str, priority: int = P_NORMAL, tags: list[str] | None = None) -> None:
        body = json.dumps({"topic": self.topic, "title": title, "message": message,
                           "priority": priority, "tags": tags or []}).encode("utf-8")
        req = urllib.request.Request(self.server, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            resp.read()


class DryRun:
    def send(self, title, message, priority=P_NORMAL, tags=None):
        print(f"[DRY-RUN] (priorité {priority}) {title} — {message}")


# --------------------------------------------------------------------------- vent
@dataclass
class WindPeak:
    gust: int
    mean: int
    when: datetime  # heure locale de l'emplacement


def fetch_wind(lat: float, lon: float, horizon_h: int) -> dict:
    days = min(16, math.ceil(horizon_h / 24) + 1)
    qs = urlencode({"latitude": lat, "longitude": lon, "hourly": "wind_gusts_10m,wind_speed_10m",
                    "wind_speed_unit": "kn", "forecast_days": days, "timezone": "auto"})
    return get_json(f"{OPEN_METEO}?{qs}")


def wind_peak(data: dict, now: datetime, horizon_h: int) -> WindPeak | None:
    """Plus forte rafale prévue dans la fenêtre [maintenant - 1 h ; maintenant + horizon]."""
    tz = timezone(timedelta(seconds=data.get("utc_offset_seconds", 0)))
    h = data["hourly"]
    best: WindPeak | None = None
    for t, gust, mean in zip(h["time"], h["wind_gusts_10m"], h["wind_speed_10m"]):
        if gust is None:
            continue
        dt = datetime.fromisoformat(t).replace(tzinfo=tz)
        if dt < now - timedelta(hours=1) or dt > now + timedelta(hours=horizon_h):
            continue
        if best is None or gust > best.gust:
            best = WindPeak(round(gust), round(mean or 0), dt)
    return best


def when_fr(dt: datetime, now: datetime) -> str:
    today = now.astimezone(dt.tzinfo).date()
    delta = (dt.date() - today).days
    day = "aujourd'hui" if delta == 0 else "demain" if delta == 1 else JOURS[dt.weekday()]
    return f"{day} {dt.hour}h"


# --------------------------------------------------------------------------- cyclones
@dataclass
class Cyclone:
    id: str
    name: str
    lat: float
    lon: float
    wind_kt: float | None
    track: list[tuple[float, float, datetime | None]] = field(default_factory=list)


def classify(wind_kt: float | None) -> tuple[str, int]:
    """(libellé, rang) — le rang sert à détecter une catégorie supérieure."""
    if wind_kt is None:
        return "Cyclone tropical", 0
    if wind_kt < 34:
        return "Dépression tropicale", 0
    if wind_kt < 64:
        return "Tempête tropicale", 1
    for cat, limit in ((1, 83), (2, 96), (3, 113), (4, 137)):
        if wind_kt < limit:
            return f"Typhon cat. {cat}", cat + 1
    return "Typhon cat. 5", 6


TIME_KEYS = ("trackdate", "forecastdate", "datetime", "date", "fromdate")


def parse_track(geojson: dict) -> list[tuple[float, float, datetime | None]]:
    pts: list[tuple[float, float, datetime | None]] = []
    for feat in geojson.get("features", []):
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        when = next((t for k in TIME_KEYS if (t := parse_time(props.get(k)))), None)
        kind, coords = geom.get("type"), geom.get("coordinates")
        if kind == "Point":
            pts.append((coords[1], coords[0], when))
        elif kind == "MultiPoint":
            pts.extend((c[1], c[0], when) for c in coords)
        elif kind == "LineString":
            pts.extend((la, lo, None) for la, lo in densify([(c[1], c[0]) for c in coords]))
    return pts


def fetch_cyclones() -> list[Cyclone]:
    data = get_json(GDACS_EVENTS)
    out: list[Cyclone] = []
    for feat in data.get("features", []):
        p = feat.get("properties") or {}
        if p.get("eventtype") != "TC" or str(p.get("iscurrent", "true")).lower() == "false":
            continue
        lon, lat = feat["geometry"]["coordinates"][:2]
        kmh = (p.get("severitydata") or {}).get("severity")
        cyc = Cyclone(id=str(p.get("eventid")), name=str(p.get("name") or p.get("eventname") or "").upper(),
                      lat=lat, lon=lon, wind_kt=(kmh / 1.852) if isinstance(kmh, (int, float)) else None)
        cyc.track.append((lat, lon, None))
        geom_url = (p.get("url") or {}).get("geometry")
        if geom_url:
            try:
                cyc.track.extend(parse_track(get_json(geom_url)))
            except Exception as exc:  # trajectoire facultative : on garde au moins la position
                log(f"trajectoire indisponible pour {cyc.name} : {exc}")
        out.append(cyc)
    return out


@dataclass
class Approach:
    dist_km: float
    lat: float
    lon: float
    when: datetime | None


def closest_approach(cyc: Cyclone, lat: float, lon: float, now: datetime) -> Approach:
    """Point le plus proche de l'emplacement : position actuelle + trajectoire à venir."""
    best = Approach(haversine_km(lat, lon, cyc.lat, cyc.lon), cyc.lat, cyc.lon, None)
    for p_lat, p_lon, when in cyc.track:
        if when is not None and when < now - timedelta(hours=3):
            continue  # point passé de la trajectoire
        d = haversine_km(lat, lon, p_lat, p_lon)
        if d < best.dist_km:
            best = Approach(d, p_lat, p_lon, when)
    return best


# --------------------------------------------------------------------------- logique d'alerte
def transition(prev: dict | None, active: bool, worse: bool) -> str | None:
    """'new' | 'worse' | 'end' | None selon l'état mémorisé."""
    if prev is None:
        return "new" if active else None
    if not active:
        return "end"
    return "worse" if worse else None


class Runner:
    def __init__(self, cfg: dict, state: dict, notifier, now: datetime,
                 wind_source=fetch_wind, cyclone_source=fetch_cyclones):
        self.cfg, self.state, self.notifier, self.now = cfg, state, notifier, now
        self.wind_source, self.cyclone_source = wind_source, cyclone_source
        self.send_errors = 0

    # -- envoi + mémorisation (l'état n'avance que si l'envoi a réussi)
    def _notify(self, title, message, priority, tags) -> bool:
        try:
            self.notifier.send(title, message, priority, tags)
            return True
        except Exception as exc:
            log(f"envoi ntfy impossible : {exc}")
            self.send_errors += 1
            return False

    def _source_ok(self, name: str) -> None:
        self.state["failures"].pop(name, None)

    def _source_failed(self, name: str, exc: Exception) -> None:
        log(f"source en échec ({name}) : {exc}")
        f = self.state["failures"].setdefault(name, {"count": 0, "warned": False})
        f["count"] += 1
        if f["count"] >= FAILURES_BEFORE_WARNING and not f["warned"]:
            if self._notify("Surveillance dégradée",
                            f"Source « {name} » injoignable depuis {f['count']} passages — "
                            "les alertes de cette source ne sont plus garanties.",
                            P_NORMAL, ["warning"]):
                f["warned"] = True

    # -- vent
    def check_wind(self, loc: dict) -> None:
        name, key = loc["nom"], f"vent:{loc['nom']}"
        horizon = int(self.cfg["horizon_heures"])
        try:
            peak = wind_peak(self.wind_source(loc["lat"], loc["lon"], horizon), self.now, horizon)
            self._source_ok(key)
        except Exception as exc:
            return self._source_failed(key, exc)
        seuil = loc["seuil_rafales_kts"]
        prev = self.state["alerts"].get(key)
        gust = peak.gust if peak else 0
        active = gust >= (seuil - self.cfg["marge_fin_kts"] if prev else seuil)
        worse = bool(prev) and gust >= prev["level"] + self.cfg["escalade_rafales_kts"]
        action = transition(prev, active, worse)
        if action is None:
            return
        if action == "end":
            if self._notify(f"Fin d'alerte vent — {name}",
                            f"{name} : plus de rafales ≥ {seuil} kts prévues dans les "
                            f"{horizon // 24} prochains jours.", P_NORMAL, ["white_check_mark"]):
                del self.state["alerts"][key]
            return
        msg = f"{name} : rafales {peak.gust} kts prévues {when_fr(peak.when, self.now)} " \
              f"(vent moyen {peak.mean} kts)"
        title = f"Vent fort — {name}" if action == "new" else f"Vent en aggravation — {name}"
        if self._notify(title, msg, P_HIGH, ["wind_face", "warning"]):
            self.state["alerts"][key] = {"level": peak.gust, "priority": P_HIGH, "since":
                                         (prev or {}).get("since") or self.now.isoformat()}

    # -- cyclones
    def check_cyclones(self) -> None:
        try:
            cyclones = self.cyclone_source()
            self._source_ok("cyclones")
        except Exception as exc:
            return self._source_failed("cyclones", exc)
        seen: set[str] = set()
        for loc in self.cfg["emplacements"]:
            for cyc in cyclones:
                key = f"cyclone:{cyc.id}:{loc['nom']}"
                ap = closest_approach(cyc, loc["lat"], loc["lon"], self.now)
                if ap.dist_km > loc["rayon_cyclone_km"]:
                    continue
                seen.add(key)
                self._cyclone_alert(loc, cyc, ap, key)
        # alertes mémorisées dont le cyclone s'est éloigné ou a disparu
        for key in [k for k in self.state["alerts"] if k.startswith("cyclone:") and k not in seen]:
            loc_name = key.split(":", 2)[2]
            if any(l["nom"] == loc_name for l in self.cfg["emplacements"]):
                prev = self.state["alerts"][key]
                if self._notify(f"Fin d'alerte cyclone — {loc_name}",
                                f"{loc_name} : {prev.get('label', 'le cyclone')} n'est plus à moins de "
                                f"{prev['radius']} km (prévision).", P_NORMAL, ["white_check_mark"]):
                    del self.state["alerts"][key]

    def _cyclone_alert(self, loc: dict, cyc: Cyclone, ap: Approach, key: str) -> None:
        name = loc["nom"]
        label, rank = classify(cyc.wind_kt)
        urgent = ap.dist_km < self.cfg["rayon_urgence_km"]
        priority = P_URGENT if urgent else P_HIGH
        prev = self.state["alerts"].get(key)
        worse = bool(prev) and (rank > prev["rank"] or priority > prev["priority"]
                                or ap.dist_km <= prev["dist"] - self.cfg["escalade_cyclone_km"])
        action = transition(prev, True, worse)
        if action is None:
            return
        parts = [f"{label} {cyc.name}".strip(), f"à {round(ap.dist_km)} km de {name}"]
        # distance affichée = point le plus proche ; direction = déplacement vers ce point
        if (ap.lat, ap.lon) != (cyc.lat, cyc.lon) and haversine_km(cyc.lat, cyc.lon, ap.lat, ap.lon) > 50:
            parts.append(f"trajectoire vers {compass_fr(bearing_deg(cyc.lat, cyc.lon, ap.lat, ap.lon))}")
        if ap.when and ap.when > self.now:
            parts.append(f"passage au plus près dans {round((ap.when - self.now).total_seconds() / 3600)} h")
        msg = ", ".join(parts)
        title = ("Cyclone — " if action == "new" else "Cyclone en aggravation — ") + name
        if self._notify(title, msg, priority, ["cyclone", "rotating_light" if urgent else "warning"]):
            self.state["alerts"][key] = {"rank": rank, "priority": priority, "dist": round(ap.dist_km),
                                         "label": f"{label} {cyc.name}".strip(), "radius": loc["rayon_cyclone_km"]}

    # -- contrôle quotidien
    def heartbeat(self) -> None:
        local = self.now.astimezone(ZoneInfo(self.cfg["fuseau"]))
        today = local.date().isoformat()
        if local.hour < self.cfg["heure_controle"] or self.state.get("last_heartbeat") == today:
            return
        n = len(self.state["alerts"])
        msg = "Surveillance OK — RAS" if n == 0 else f"Surveillance OK — {n} alerte(s) en cours"
        if self._notify("Contrôle quotidien", msg, P_LOW, ["heavy_check_mark"]):
            self.state["last_heartbeat"] = today

    def run(self) -> int:
        for loc in self.cfg["emplacements"]:
            self.check_wind(loc)
        self.check_cyclones()
        self.heartbeat()
        return 1 if self.send_errors else 0


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml", type=Path)
    ap.add_argument("--state", default="state.json", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="affiche les notifications au lieu de les envoyer (état non écrit)")
    ap.add_argument("--test", action="store_true", help="envoie une notification de test et s'arrête")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    notifier = DryRun() if args.dry_run else Ntfy(cfg["ntfy_server"], cfg["ntfy_topic"])
    if args.test:
        notifier.send("Test weather-alerts", "Si vous lisez ceci, les notifications fonctionnent.", P_NORMAL, ["tada"])
        return 0
    state = load_state(args.state)
    code = Runner(cfg, state, notifier, datetime.now(timezone.utc)).run()
    if not args.dry_run:
        save_state(args.state, state)
    return code


if __name__ == "__main__":
    sys.exit(main())
