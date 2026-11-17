"""Cricket match prediction using KP astrology - Streamlit UI.  Run: streamlit run app.py"""
import json
from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from astrology import planetary_positions as pp
from config import AYANAMSAS, DEFAULT_WEIGHTS, HOUSE_SYSTEMS, NODE_TYPES, HouseRules
from prediction.match_prediction import analyze, report_rows
from utils.datetime_utils import localize
from utils.validation import (validate_horary_number, validate_lat_lon, validate_timezone,
                              validate_longitude_table)
from utils.visualization import category_chart, probability_bar, radar

st.set_page_config(page_title="KP Cricket Prediction", layout="wide")
st.title("Cricket Match Prediction - KP Astrology")
st.caption("Model-derived astrological support scores. They are NOT scientifically validated "
           "cricket probabilities and never a guaranteed outcome.")
if not pp.HAVE_ENGINE:
    st.warning("PyEphem is not installed (pip install ephem): automatic planetary calculation is "
               "unavailable. Enter positions manually under Match Details > Manual overrides.")

try:  # optional: browser GPS (pip install streamlit-geolocation)
    from streamlit_geolocation import streamlit_geolocation
except ImportError:
    streamlit_geolocation = None

SEC = 1  # time pickers step in 1 second so the seconds column is shown
st.session_state.setdefault("lat", 19.076)
st.session_state.setdefault("lon", 72.8777)
st.session_state.setdefault("hlat", st.session_state["lat"])
st.session_state.setdefault("hlon", st.session_state["lon"])


def gps_block():
    """Fill latitude/longitude from the device's GPS (browser geolocation; needs permission)."""
    if streamlit_geolocation is None:
        st.caption("GPS: run `pip install streamlit-geolocation` and restart to enable "
                   "'Use my GPS location'.")
        return
    st.caption("Click the button, allow location access in the browser, and the coordinates fill in.")
    loc = streamlit_geolocation()
    la, lo = (loc or {}).get("latitude"), (loc or {}).get("longitude")
    if la is not None and lo is not None and (la, lo) != st.session_state.get("gps_last"):
        st.session_state.update(gps_last=(la, lo), lat=round(la, 4), lon=round(lo, 4),
                                hlat=round(la, 4), hlon=round(lo, 4))
        st.rerun()
    if la is not None:
        acc = (loc or {}).get("accuracy")
        st.success(f"GPS location: {la:.4f}, {lo:.4f}" + (f" (accuracy ~{acc:.0f} m)" if acc else ""))


PLANET_ROWS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu"]


def manual_block(key):
    on = st.checkbox(f"Manual planets/cusps ({key})", key=f"man_{key}")
    if not on:
        return None
    st.caption("Sidereal longitudes 0-360. Ketu is derived from Rahu.")
    p = st.data_editor(pd.DataFrame({"Planet": PLANET_ROWS, "Longitude": [None] * 8}),
                       key=f"mp_{key}", hide_index=True, disabled=["Planet"])
    c = st.data_editor(pd.DataFrame({"House": range(1, 13), "Cusp longitude": [None] * 12}),
                       key=f"mc_{key}", hide_index=True, disabled=["House"])
    planets = {r.Planet: float(r.Longitude) for r in p.itertuples() if pd.notna(r.Longitude)}
    cusps = [float(x) for x in c["Cusp longitude"] if pd.notna(x)]
    return {"planets": planets or None, "cusps": cusps or None}


with st.sidebar:
    st.header("Advanced Settings")
    ay = st.selectbox("Ayanamsa", list(AYANAMSAS))
    hs = st.selectbox("House system", list(HOUSE_SYSTEMS))
    node = st.selectbox("Node", list(NODE_TYPES))
    st.subheader("Weights (configurable)")
    weights = {k: st.slider(k, 0, 100, int(v * 100)) / 100 for k, v in DEFAULT_WEIGHTS.items()}
    st.subheader("House assignments")
    own_a = st.number_input("Team A house", 1, 12, 1)
    own_b = st.number_input("Team B house", 1, 12, 7)
    sp = st.multiselect("Shared positive houses", range(1, 13), [5, 6, 10, 11])
    sn = st.multiselect("Shared negative houses", range(1, 13), [8, 12])

tabs = st.tabs(["Match Details", "Planetary Positions", "KP House Analysis", "Ruling Planets",
                "Horary Analysis", "Transit Analysis", "Dasha Analysis", "Team Comparison",
                "Final Prediction"])

with tabs[0]:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Match")
        ta = st.text_input("Team A", "Team A")
        tb = st.text_input("Team B", "Team B")
        md = st.date_input("Match date", date.today())
        mt = st.time_input("Scheduled start (HH:MM:SS)", time(14, 0), step=SEC)
        place = st.text_input("Location", "")
        gps_block()
        lat = st.number_input("Latitude", -90.0, 90.0, format="%.4f", key="lat")
        lon = st.number_input("Longitude", -180.0, 180.0, format="%.4f", key="lon")
        tzn = st.text_input("Time zone (IANA)", "Asia/Kolkata")
        use_act = st.checkbox("Actual commencement time known")
        ad, at = st.date_input("Actual start date", md, disabled=not use_act), st.time_input("Actual start time (HH:MM:SS)", mt, step=SEC, disabled=not use_act)
    with c2:
        st.subheader("KP Horary")
        hd = st.date_input("Question date", date.today())
        ht = st.time_input("Question time (HH:MM:SS)", datetime.now().time().replace(microsecond=0), step=SEC)
        hn = st.number_input("Horary number (1-249)", 1, 249, 1)
        hlat = st.number_input("Horary latitude", -90.0, 90.0, format="%.4f", key="hlat")
        hlon = st.number_input("Horary longitude", -180.0, 180.0, format="%.4f", key="hlon")
        st.subheader("Manual overrides")
        man_match = manual_block("match")
        man_hor = manual_block("horary")
        use_md = st.checkbox("Manual Dasha/Bhukti/Antara")
        from astrology.kp_calculations import DASHA_ORDER
        mdl = [st.selectbox(l, DASHA_ORDER, key=f"md{l}", disabled=not use_md) for l in ("Mahadasha", "Bhukti", "Antara")]

go = st.button("ANALYZE MATCH", type="primary")
if go:
    try:
        tz = validate_timezone(tzn)
        validate_lat_lon(lat, lon)
        validate_lat_lon(hlat, hlon)
        validate_horary_number(hn)
        for m in (man_match, man_hor):
            if m and m["planets"]:
                validate_longitude_table(m["planets"])
        start = localize(ad, at, tz) if use_act else localize(md, mt, tz)
        form = {"team_a": ta, "team_b": tb, "match_dt": start, "lat": lat, "lon": lon,
                "hor_dt": localize(hd, ht, tz), "hor_lat": hlat, "hor_lon": hlon, "hor_number": hn,
                "actual_start_given": use_act,
                "settings": {"ayanamsa": ay, "house_system": hs, "node": NODE_TYPES[node],
                             "ephe_path": None},
                "weights": weights,
                "rules": HouseRules(int(own_a), int(own_b), tuple(sp), tuple(sn)),
                "manual": {"match": man_match or {}, "horary": man_hor or {}},
                "manual_dasha": tuple(mdl) if use_md else None}
        st.session_state["rep"] = analyze(form)
    except Exception as e:
        st.error(f"Input error: {e}")

rep = st.session_state.get("rep")
if not rep:
    for t in tabs[1:]:
        with t:
            st.info("Click ANALYZE MATCH first.")
    st.stop()

names = rep["names"]
res = rep["result"]
mc, hc = rep["charts"].get("match"), rep["charts"].get("horary")


def chart_df(ch):
    rows = [{"Planet": n, "Longitude": round(p.longitude, 4), "Sign": p.info.sign,
             "Sign Lord": p.info.sign_lord, "Nakshatra": p.info.nakshatra, "Star": p.info.star_lord,
             "Sub": p.info.sub_lord, "Sub-sub": p.info.sub_sub_lord, "Retro": p.retrograde,
             "House": ch.house_of(p.longitude), "Source": p.source} for n, p in ch.planets.items()]
    cu = [{"House": i + 1, "Cusp": round(ch.cusps[i], 4), "Sign": c.sign, "Sign Lord": c.sign_lord,
           "Star": c.star_lord, "Sub": c.sub_lord} for i, c in enumerate(ch.cusp_info)]
    return pd.DataFrame(rows), pd.DataFrame(cu)


with tabs[1]:
    st.write({"Ayanamsa": ay, "House system": hs, "Node": node})
    for k, ch in rep["charts"].items():
        if ch:
            st.subheader(ch.label)
            a, b = chart_df(ch)
            st.dataframe(a, width="stretch")
            st.dataframe(b, width="stretch")
            for n in ch.notes:
                st.caption(n)
    for u in rep["unavailable"]:
        st.warning(u)

with tabs[2]:
    c = next(x for x in rep["categories"] if x["name"] == "KP House Significators")
    if c["available"]:
        st.dataframe(pd.DataFrame([{"Planet": e["planet"], "Houses": e["houses"],
                                    f"{names[0]} +": e["A"]["pos"], f"{names[0]} -": e["A"]["neg"],
                                    f"{names[1]} +": e["B"]["pos"], f"{names[1]} -": e["B"]["neg"]}
                                   for e in c["evidence"]]), width="stretch")
    else:
        st.warning(c["reason"])

with tabs[3]:
    if "rp" in rep:
        st.dataframe(pd.DataFrame(rep["rp"]["table"]), width="stretch")
        st.caption(f"Match RP day lord: {rep['rp']['match']['note']}; horary: {rep['rp']['horary']['note']}")
    else:
        st.warning("Ruling planets unavailable.")

with tabs[4]:
    if "csl" in rep["tables"]:
        st.dataframe(pd.DataFrame(rep["tables"]["csl"]), width="stretch")
        st.info(rep["csl_conclusion"])
    else:
        st.warning("Horary analysis unavailable.")

with tabs[5]:
    if "transit" in rep["tables"]:
        st.dataframe(pd.DataFrame(rep["tables"]["transit"]), width="stretch")
    else:
        st.warning("Transit analysis unavailable.")

with tabs[6]:
    if "dasha" in rep:
        st.dataframe(pd.DataFrame([{"Level": k, **{kk: str(vv) for kk, vv in v.items()}}
                                   for k, v in rep["dasha"].items()]), width="stretch")
    else:
        st.warning("Dasha analysis unavailable (no reference data).")

with tabs[7]:
    st.dataframe(pd.DataFrame([{"Category": c["name"], "Weight": weights.get(c["name"], 0),
                                names[0]: c["A"], names[1]: c["B"], "Available": c["available"],
                                "Note": c["reason"]} for c in rep["categories"]]),
                 width="stretch")
    if any(c["available"] for c in rep["categories"]):
        st.plotly_chart(category_chart(names, rep["categories"]), width="stretch")
        st.plotly_chart(radar(names, rep["categories"]), width="stretch")

with tabs[8]:
    st.subheader("Match Prediction Summary")
    if res["pA"] is None:
        st.error("No analysis produced data, so no winner can be named. Check the inputs / "
                 "Unavailable list below.")
    else:
        winner, wp = (names[0], res["pA"]) if res["pA"] >= res["pB"] else (names[1], res["pB"])
        st.metric("Predicted winner", winner, f"{wp:.1f}% win probability")
        a, b = st.columns(2)
        a.metric(f"{names[0]} win probability", f"{res['pA']:.1f}%")
        b.metric(f"{names[1]} win probability", f"{res['pB']:.1f}%")
        st.plotly_chart(probability_bar(names, res["pA"], res["pB"]), width="stretch")
    st.write(res["explanation"])
    for key, title in (("inn1", "First Innings"),):
        if key in rep:
            st.write(f"**{title}:** favoured {rep[key]['favoured']}. {rep[key]['note']}")
        else:
            st.write(f"**{title}:** unavailable.")
    if "toss" in rep:
        t = rep["toss"]
        st.write(f"**Predicted toss winner:** {t['winner']} | "
                 f"**Predicted to bat first:** {t['bat']['batting_first']} "
                 f"(toss winner elects to {t['bat']['decision']})")
        st.caption(t["note"] + " Batting decision is a heuristic: " + t["bat"]["basis"])
    st.subheader("Score contributions")
    st.dataframe(pd.DataFrame(res["contributions"]), width="stretch")
    if "csl_conclusion" in rep:
        st.write("**KP Horary:** " + rep["csl_conclusion"])
    st.subheader("Unavailable / provisional")
    for u in rep["unavailable"]:
        st.warning(u)
    df = pd.DataFrame(report_rows(rep))
    st.download_button("Download prediction CSV", df.to_csv(index=False), "kp_prediction.csv")
    full = {"result": {k: v for k, v in res.items() if k != "categories"},
            "evidence": report_rows(rep), "unavailable": rep["unavailable"]}
    st.download_button("DOWNLOAD FULL KP ANALYSIS", json.dumps(full, indent=2, default=str),
                       "kp_full_analysis.json")
