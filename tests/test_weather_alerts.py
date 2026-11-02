import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import weather_alerts as wa  # noqa: E402

NOW = datetime(2026, 11, 2, 0, 7, tzinfo=timezone.utc)  # 08:07 à Manille


class Rec:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, title, message, priority=3, tags=None):
        if self.fail:
            raise OSError("ntfy down")
        self.sent.append((title, message, priority))


def cfg(**kw):
    c = {**wa.DEFAULTS, "ntfy_topic": "t", "heure_controle": 99,
         "emplacements": [{"nom": "Baie", "lat": 9.07, "lon": 123.12, **wa.LOC_DEFAULTS}]}
    c.update(kw)
    return c


def wind_data(gust, mean=28, hours_ahead=60):
    start = NOW.astimezone(timezone(timedelta(hours=8))).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    times = [(start + timedelta(hours=i)).isoformat(timespec="minutes") for i in range(120)]
    g = [10] * 120
    g[hours_ahead] = gust
    return {"utc_offset_seconds": 28800,
            "hourly": {"time": times, "wind_gusts_10m": g, "wind_speed_10m": [mean] * 120}}


def run(state, wind=None, cyclones=None, n=None, **kw):
    n = n or Rec()
    r = wa.Runner(cfg(**kw), state, n, NOW,
                  wind_source=lambda *a: wind_data(*(wind or (10,))),
                  cyclone_source=lambda: cyclones or [])
    r.run()
    return n.sent


def test_threshold_is_inclusive_and_dedup():
    st = wa.load_state(Path("/nonexistent"))
    sent = run(st, (35,))
    assert len(sent) == 1 and sent[0][2] == wa.P_HIGH and "rafales 35 kts" in sent[0][1]
    assert run(st, (36,)) == []            # pas d'aggravation notable
    assert len(run(st, (41,))) == 1        # +6 kts -> aggravation


def test_below_threshold_silent_then_end_with_hysteresis():
    st = wa.load_state(Path("/nonexistent"))
    assert run(st, (34,)) == []
    run(st, (40,))
    assert run(st, (34,)) == []            # 34 >= 35-2 : toujours en alerte
    end = run(st, (20,))
    assert len(end) == 1 and "Fin d'alerte" in end[0][0] and not st["alerts"]


def test_message_has_day_and_hour():
    st = wa.load_state(Path("/nonexistent"))
    msg = run(st, (41, 28, 60))[0][1]       # 60 h après 08h -> mercredi 4 nov 20h
    assert "mercredi 20h" in msg and "vent moyen 28 kts" in msg


def cyclone(lat, lon, kt=50, track=()):
    return wa.Cyclone("1", "TEST", lat, lon, kt, [(lat, lon, None), *track])


def test_cyclone_radius_urgent_and_escalation():
    st = wa.load_state(Path("/nonexistent"))
    far = cyclone(9.07, 127.5)              # ~480 km
    s = run(st, cyclones=[far])
    assert s and s[0][2] == wa.P_HIGH
    assert run(st, cyclones=[cyclone(9.07, 127.4)]) == []      # quasi pas bougé
    near = cyclone(9.07, 124.5)             # ~150 km -> urgent
    s = run(st, cyclones=[near])
    assert s and s[0][2] == wa.P_URGENT
    s = run(st, cyclones=[])                # disparu -> fin
    assert "Fin d'alerte" in s[0][0] and not st["alerts"]


def test_cyclone_category_upgrade_renotifies():
    st = wa.load_state(Path("/nonexistent"))
    run(st, cyclones=[cyclone(9.07, 127.5, kt=50)])
    s = run(st, cyclones=[cyclone(9.07, 127.5, kt=90)])
    assert s and "Typhon cat. 2" in s[0][1]


def test_forecast_track_triggers_and_past_points_ignored():
    st = wa.load_state(Path("/nonexistent"))
    fut = (9.07, 123.5, NOW + timedelta(hours=30))
    past = (9.07, 123.3, NOW - timedelta(hours=48))
    s = run(st, cyclones=[cyclone(9.07, 135, track=[fut, past])])
    assert s and "30 h" in s[0][1] and "trajectoire vers l'ouest" in s[0][1]
    st2 = wa.load_state(Path("/nonexistent"))
    assert run(st2, cyclones=[cyclone(9.07, 135, track=[past])]) == []


def test_send_failure_keeps_state_for_retry():
    st = wa.load_state(Path("/nonexistent"))
    run(st, (45,), n=Rec(fail=True))
    assert not st["alerts"]
    assert len(run(st, (45,))) == 1


def test_heartbeat_once_per_day():
    st = wa.load_state(Path("/nonexistent"))
    assert [m for m in run(st, heure_controle=8) if m[2] == wa.P_LOW]
    assert run(st, heure_controle=8) == []


def test_source_failure_warns_after_six():
    st = wa.load_state(Path("/nonexistent"))
    n = Rec()
    def boom(*a): raise RuntimeError("down")
    for _ in range(7):
        wa.Runner(cfg(), st, n, NOW, wind_source=boom, cyclone_source=lambda: []).run()
    assert sum("dégradée" in t for t, _, _ in n.sent) == 1


def test_parse_track_and_classify():
    gj = {"features": [
        {"geometry": {"type": "Point", "coordinates": [124, 10]}, "properties": {"trackdate": "2026-11-03T00:00:00Z"}},
        {"geometry": {"type": "LineString", "coordinates": [[124, 10], [126, 10]]}, "properties": {}},
        {"geometry": {"type": "Polygon", "coordinates": []}, "properties": {}}]}
    pts = wa.parse_track(gj)
    assert pts[0][2] is not None and len(pts) > 3
    assert wa.classify(30)[0] == "Dépression tropicale" and wa.classify(100)[0] == "Typhon cat. 3"
