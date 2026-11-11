"""Suggestions for the period on screen: finds the moments in a stretch of readings where the automatic control could not keep the
orchids inside their targets, and says what a grower could do. Pure Python, no Django, deterministic (same readings, same suggestions).

Each suggestion: kind, severity (info | advice | warning), title, detail (plain words with the numbers), start and end (ISO, simulated
time) and a short `action` for the grower. Windows work on the same samples the console charts draw, so what is suggested always
matches the timeline that is displayed. Orchids grow in bark or moss, so the sensor is called "medium moisture".
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from greengrow_common import safety_limits as L

SAMPLE_H = 0.5                       # one sample per 30 simulated minutes
MERGE_GAP = 6                        # samples (3 h): episodes closer than this are one episode
COLD_MARGIN_C = 1.0                  # flag a fall to within this many degrees of the safety minimum (the controller normally holds 1.5-2 C above it)
SEVERITY_ORDER = {"warning": 0, "advice": 1, "info": 2}


def _season(t: datetime) -> str:
    return "rest" if t.month in L.REST_MONTHS else "growing"


def _episodes(rows: list[dict], pred, min_samples: int) -> list[list[dict]]:
    """Runs of samples where pred is true, joined across short gaps, at least min_samples long."""
    out, cur, gap = [], [], 0
    for r in rows:
        if pred(r):
            cur.append(r)
            gap = 0
        elif cur:
            gap += 1
            if gap > MERGE_GAP:
                out.append(cur)
                cur, gap = [], 0
    if cur:
        out.append(cur)
    return [e for e in out if len(e) >= min_samples]


def _item(kind, severity, title, detail, start, end, action):
    return {"kind": kind, "severity": severity, "title": title, "detail": detail, "action": action,
            "start": start.isoformat(), "end": end.isoformat()}


def detect(rows: list[dict], latest_feed_age_days: float | None = None, is_current: bool = True) -> list[dict]:
    """rows: [{t: datetime, temp, outside, rh, moisture, light}] in time order. latest_feed_age_days: days since the last
    manual feeding (None = never recorded). is_current: the window ends at the present, so reminders apply."""
    if not rows:
        return []
    out: list[dict] = []
    first, last = rows[0]["t"], rows[-1]["t"]

    # 1. heat spells: above the top of the day band for at least 2 hours
    top = L.DAY_BAND_C[1]
    for e in _episodes(rows, lambda r: r["temp"] > top, 4):
        peak = max(r["temp"] for r in e)
        out_peak = max((r["outside"] for r in e if r["outside"] is not None), default=None)
        hours = len(e) * SAMPLE_H
        out.append(_item(
            "heat", "warning" if peak > top + 2 else "advice", "Heat spell",
            f"The greenhouse stayed above {top:g} C for about {hours:g} h and peaked at {peak:.1f} C"
            + (f" (outside {out_peak:.1f} C)" if out_peak is not None else "") + ". The vent cannot cool below the outside air.",
            e[0]["t"], e[-1]["t"],
            "Damp down the floor and benches, add shade cloth, keep the vent fully open, and water early in the day."))

    # 2. cold: close to the safety minimum
    for e in _episodes(rows, lambda r: r["temp"] < L.COLD_DANGER_C[_season(r["t"])] + COLD_MARGIN_C, 2):
        low = min(r["temp"] for r in e)
        floor = L.COLD_DANGER_C[_season(e[0]["t"])]
        out.append(_item(
            "cold", "warning", "Close to the safety minimum",
            f"The greenhouse fell to {low:.1f} C, within {low - floor:.1f} C of the {floor:g} C safety minimum.",
            e[0]["t"], e[-1]["t"],
            "Check the heater and the vent seals, and move plants away from the glass; consider a second heat source on cold nights."))

    # per-night lows and per-day light
    nights: dict = defaultdict(list)
    days: dict = defaultdict(list)
    for r in rows:
        h = r["t"].hour
        if h >= 22 or h < 6:
            nights[(r["t"] - timedelta(hours=6)).date()].append(r)
        days[r["t"].date()].append(r)

    # 3. warm nights: the night drop (a flowering cue) is lost
    warm = []
    for d, rs in sorted(nights.items()):
        if len(rs) < 12:
            continue
        season = _season(rs[0]["t"])
        low = min(r["temp"] for r in rs)
        if season == "growing" and low > L.NIGHT_BAND_C["growing"][1]:
            warm.append((d, low, rs))
    if len(warm) >= 2:
        worst = max(w[1] for w in warm)
        out.append(_item(
            "warm_nights", "advice", "Warm nights",
            f"{len(warm)} nights had a low above the {L.NIGHT_BAND_C['growing'][1]:g} C top of the night band (up to {worst:.1f} C). "
            "Without a cooler night the day-night difference that cues flowering is small.",
            warm[0][2][0]["t"], warm[-1][2][-1]["t"],
            "Open the vent and shade the glass earlier in the evening so stored heat escapes; a cooler spot at night helps buds."))

    # 4. dull and bright days (complete days only)
    full = {d: rs for d, rs in days.items() if len(rs) >= 46}
    dli = {d: sum(r["light"] for r in rs) / L.LUX_PER_UMOL * 1800 / 1e6 for d, rs in full.items()}
    dull = [d for d, v in sorted(dli.items()) if v < L.DLI_BAND[0] and full[d][0]["t"].month not in L.LAMP_MONTHS]
    if dull:
        out.append(_item(
            "dull_days", "advice", "Dull days with no grow light",
            f"{len(dull)} day(s) got less than {L.DLI_BAND[0]:g} mol/m2/day (lowest {min(dli[d] for d in dull):.1f}) and the grow light is off outside "
            f"{_months(L.LAMP_MONTHS)}.",
            full[dull[0]][0]["t"], full[dull[-1]][-1]["t"],
            "Run the grow light for the dull day(s), or accept a short dip; orchids cope with a few dull days."))
    bright = [d for d, v in sorted(dli.items()) if v > L.DLI_BAND[1] and full[d][0]["t"].month not in L.SHADE_MONTHS]
    if bright:
        out.append(_item(
            "bright_days", "advice", "Bright days with no shade",
            f"{len(bright)} day(s) got more than {L.DLI_BAND[1]:g} mol/m2/day (highest {max(dli[d] for d in bright):.1f}) and the shade is only used in "
            f"{_months(L.SHADE_MONTHS)}. Leaf scorch is a risk above about 37 klux.",
            full[bright[0]][0]["t"], full[bright[-1]][-1]["t"],
            "Close the shade cloth on bright days, or move the most sensitive plants out of the direct sun."))

    # 5. a dry medium for a day or more
    for e in _episodes(rows, lambda r: r["moisture"] < 5.0, 48):
        rest = _season(e[0]["t"]) == "rest"
        out.append(_item(
            "dry_medium", "advice", "Medium very dry",
            f"The bark/moss medium read below 5% for about {len(e) * SAMPLE_H / 24:.1f} days"
            + (" (the winter rest allows it to dry, but roots dry out completely if it lasts)." if rest else " in the growing season."),
            e[0]["t"], e[-1]["t"],
            "Check the roots and the medium by hand; give a light extra watering if the roots look shrivelled." if rest
            else "Give a thorough soak now and check the interval between waterings."))

    # 6. dry air by day
    for e in _episodes(rows, lambda r: r["rh"] < L.HUMIDITY_BAND[0] and 9 <= r["t"].hour < 17, 6):
        out.append(_item(
            "dry_air", "info", "Dry air by day",
            f"Daytime humidity stayed under {L.HUMIDITY_BAND[0]:g}% for about {len(e) * SAMPLE_H:g} h (lowest {min(r['rh'] for r in e):.0f}%).",
            e[0]["t"], e[-1]["t"],
            "Damp down the floor, group the pots on a tray of wet gravel, or check that the humidifier has water."))

    # 7. season changeovers inside the window
    for boundary, text, action in (
        ((12, 1), "The winter rest begins (December): water much less, and stop feeding.",
         "Stop feeding, water only when the medium is nearly dry, and keep the nights cool (10-14 C)."),
        ((3, 1), "The growing season begins (March): normal watering and feeding resume.",
         "Resume weekly, dilute feeding and thorough soaks as the medium dries."),
    ):
        day = datetime(first.year if first.month <= boundary[0] else first.year + 1, boundary[0], boundary[1], tzinfo=first.tzinfo)
        if first <= day <= last:
            out.append(_item("season_change", "info", "Season changeover", text, day, day, action))

    # 8. feeding is manual: remind when overdue (only for the present)
    if is_current and _season(last) == "growing" and (latest_feed_age_days is None or latest_feed_age_days > 8):
        age = "no feeding has been recorded" if latest_feed_age_days is None else f"the last feeding was {latest_feed_age_days:.0f} days ago"
        out.append(_item(
            "feed_due", "advice", "Feeding is due",
            f"Feeding is manual in the growing season ('weakly, weekly') and {age}.",
            last, last, "Feed with a dilute fertiliser at the next watering (the Feed button in the console)."))

    out.sort(key=lambda s: (SEVERITY_ORDER[s["severity"]], s["start"]))
    return out


def _months(months) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ms = set(months)
    start = next(m for m in sorted(ms) if (m - 2) % 12 + 1 not in ms)       # the month whose predecessor is not in the set
    end = next(m for m in sorted(ms) if m % 12 + 1 not in ms)
    return f"{names[start - 1]} to {names[end - 1]}"
