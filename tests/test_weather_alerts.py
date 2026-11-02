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


def test_title_prefix():
    assert wa.full_title("Vent fort — Baie") == "Weather Alerts — Vent fort — Baie"


BULLETIN = """<html><head><script>var signal = "Signal No. 5 Negros Oriental";</script></head><body>
<h2>Tropical Cyclone Bulletin #7 — Typhoon "ADA"</h2>
<h3>TROPICAL CYCLONE WIND SIGNALS IN EFFECT</h3>
<table><tr><th>Wind Signal No. 2</th>
<td>Visayas: Cebu, the northern portion of Negros Oriental (Guihulngan City, Vallehermoso); Bohol</td></tr>
<tr><th>Wind Signal No. 1</th>
<td>Visayas: Siquijor, the rest of Negros Oriental, Negros Occidental. Wind Signal No. 1 over Antique is now lifted.</td></tr>
</table><p>Wind Signal No. 3 may be raised over Bohol.</p>
<h3>HAZARDS AFFECTING LAND AREAS</h3><p>Heavy rainfall over Negros Oriental and Leyte.</p></body></html>"""


def test_pagasa_parsing():
    signals = wa.parse_pagasa_signals(wa.html_to_text(BULLETIN))
    level, clause = wa.pagasa_signal_for(signals, "Negros Oriental")
    assert level == 2 and "northern portion of Negros Oriental (Guihulngan City" in clause
    assert wa.pagasa_signal_for(signals, "negros occidental")[0] == 1
    assert wa.pagasa_signal_for(signals, "Siquijor")[0] == 1
    assert wa.pagasa_signal_for(signals, "Antique") is None      # levé
    assert wa.pagasa_signal_for(signals, "Bohol")[0] == 2         # « may be raised » ignoré
    assert wa.pagasa_signal_for(signals, "Leyte") is None         # section dangers ignorée


def run_pagasa(st, signals, n=None):
    n = n or Rec()
    c = cfg()
    c["emplacements"][0].update(province="Negros Oriental")
    wa.Runner(c, st, n, NOW, wind_source=lambda *a: wind_data(10), cyclone_source=lambda: [],
              pagasa_source=lambda: signals).run()
    return n.sent


def test_pagasa_alert_lifecycle():
    st = wa.load_state(Path("/nonexistent"))
    assert run_pagasa(st, []) == []
    s = run_pagasa(st, [(1, "Negros Oriental")])
    assert s[0][2] == wa.P_HIGH and "Signal PAGASA n° 1" in s[0][0] and "36 h" in s[0][1]
    assert run_pagasa(st, [(1, "Negros Oriental")]) == []
    s = run_pagasa(st, [(3, "Negros Oriental")])
    assert s[0][2] == wa.P_URGENT and "relevé au n° 3" in s[0][0]
    assert run_pagasa(st, [(2, "Negros Oriental")]) == []         # baisse : silencieuse
    assert len(run_pagasa(st, [(3, "Negros Oriental")])) == 1     # remonte : notifié
    s = run_pagasa(st, [(1, "Cebu")])
    assert "Fin de signal" in s[0][0] and not st["alerts"]


def test_env_overrides_and_token(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text('emplacements: [{nom: A, lat: 1, lon: 2}]\n')
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example.org/")
    monkeypatch.setenv("NTFY_TOPIC", "alertes")
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    c = wa.load_config(p)
    n = wa.Ntfy(c["ntfy_server"], c["ntfy_topic"], c["ntfy_token"])
    assert n.server == "https://ntfy.example.org" and n.headers()["Authorization"] == "Bearer tk_secret"
    monkeypatch.delenv("NTFY_TOKEN")
    assert "Authorization" not in wa.Ntfy("s", "t", wa.load_config(p)["ntfy_token"]).headers()


def test_healthcheck(tmp_path):
    state = tmp_path / "state.json"
    assert wa.healthcheck(state, 60) == 1
    wa.last_run_path(state).touch()
    assert wa.healthcheck(state, 60) == 0


MET_RAW = {"properties": {"timeseries": [
    {"time": "2026-11-02T00:00:00Z", "data": {"instant": {"details": {"wind_speed": 5.0, "wind_speed_of_gust": 9.0}}}},
    {"time": "2026-11-04T12:00:00Z", "data": {"instant": {"details": {"wind_speed": 14.4, "wind_speed_of_gust": 21.1}}}},
    {"time": "2026-11-05T00:00:00Z", "data": {"instant": {"details": {"wind_speed": 3.0}}}},
]}}


def test_met_norway_conversion():
    data = wa.met_norway_to_hourly(MET_RAW, 123.12)
    peak = wa.wind_peak(data, NOW, 120)
    assert peak.gust == 41 and peak.mean == 28                    # m/s -> kts
    assert peak.when.hour == 20 and peak.when.utcoffset() == timedelta(hours=8)  # 12h UTC = 20h Manille
    import pytest
    with pytest.raises(RuntimeError):
        wa.met_norway_to_hourly({"properties": {"timeseries": [MET_RAW["properties"]["timeseries"][2]]}}, 123)


def test_wind_fallback_order():
    def down(*a): raise RuntimeError("503")
    def met(*a): return {"source": "MET Norway"}
    assert wa.fetch_wind(9, 123, 120, sources=(down, met))["source"] == "MET Norway"
    import pytest
    with pytest.raises(RuntimeError, match="503"):
        wa.fetch_wind(9, 123, 120, sources=(down, down))


def test_fallback_source_named_in_message():
    st = wa.load_state(Path("/nonexistent"))
    n = Rec()
    wa.Runner(cfg(), st, n, NOW, wind_source=lambda *a: wa.met_norway_to_hourly(MET_RAW, 123.12),
              cyclone_source=lambda: []).run()
    assert "rafales 41 kts prévues mercredi 20h" in n.sent[0][1] and "secours : MET Norway" in n.sent[0][1]


def test_met_norway_uses_location_timezone():
    data = wa.met_norway_to_hourly(MET_RAW, -176.65, "America/Adak")
    assert data["utc_offset_seconds"] in (-10 * 3600, -9 * 3600)   # HST-Aléoutiennes (heure d'été : -9)
    assert wa.met_norway_to_hourly(MET_RAW, 123.12)["utc_offset_seconds"] == 8 * 3600


def test_example_config_loads(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    c = wa.load_config(Path(__file__).resolve().parent.parent / "config.example.yaml")
    names = [l["nom"] for l in c["emplacements"]]
    assert names == ["Tambobo Bay", "Dumaguete"]
