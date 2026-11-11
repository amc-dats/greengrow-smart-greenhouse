"""Suggestions for the period on screen: each scenario found in the simulated year, and nothing when nothing is wrong."""
from datetime import datetime, timedelta, timezone

from greenhouse import insights


def series(start, hours, **kw):
    """Half-hourly samples from `start`; each keyword is a value or a function of the hour index."""
    out = []
    for i in range(int(hours * 2)):
        t = start + timedelta(minutes=30 * i)
        row = {"t": t, "temp": 22.0, "outside": 12.0, "rh": 65.0, "moisture": 35.0, "light": 20000.0}
        for k, v in kw.items():
            row[k] = v(t) if callable(v) else v
        out.append(row)
    return out


JUN = datetime(2026, 6, 10, tzinfo=timezone.utc)
JAN = datetime(2026, 1, 10, tzinfo=timezone.utc)
APR = datetime(2026, 4, 10, tzinfo=timezone.utc)


def kinds(rows, **kw):
    return {s["kind"] for s in insights.detect(rows, **kw)}


def test_nothing_to_suggest_when_everything_is_in_range():
    rows = series(JUN, 24 * 3, temp=lambda t: 24.0 if 7 <= t.hour < 19 else 16.5, light=lambda t: 25000.0 if 7 <= t.hour < 19 else 0.0)
    assert insights.detect(rows, latest_feed_age_days=2) == []
    assert insights.detect([]) == []


def test_heat_spell_names_the_peak_and_the_outside_air():
    rows = series(JUN, 24, temp=lambda t: 31.0 if 12 <= t.hour < 17 else 24.0, outside=lambda t: 34.0 if 12 <= t.hour < 17 else 20.0)
    s = [x for x in insights.detect(rows, latest_feed_age_days=1) if x["kind"] == "heat"]
    assert len(s) == 1 and s[0]["severity"] == "warning" and "31.0" in s[0]["detail"] and "34.0" in s[0]["detail"]


def test_a_short_blip_is_not_a_heat_spell():
    rows = series(JUN, 24, temp=lambda t: 29.0 if t.hour == 13 and t.minute == 0 else 24.0)
    assert "heat" not in kinds(rows, latest_feed_age_days=1)


def test_close_to_the_safety_minimum_is_a_warning_with_the_season_floor():
    rest = series(JAN, 24, temp=lambda t: 10.7 if 2 <= t.hour < 5 else 14.0)
    s = [x for x in insights.detect(rest) if x["kind"] == "cold"]
    assert len(s) == 1 and s[0]["severity"] == "warning" and "10 C" in s[0]["detail"]
    assert "cold" not in kinds(series(JAN, 24, temp=14.0))                      # 14 C is fine in the rest, but would be too cold in the growing season
    assert "cold" in kinds(series(JUN, 24, temp=15.5), latest_feed_age_days=1)   # 15.5 C is within 1 C of the 15 C minimum


def test_warm_nights_need_at_least_two():
    one = series(JUN, 48, temp=lambda t: 21.0 if (t.day == 10 and t.hour >= 22) or (t.day == 11 and t.hour < 6) else (24.0 if 7 <= t.hour < 19 else 16.5))
    two = series(JUN, 72, temp=lambda t: 21.0 if (t.hour >= 22 or t.hour < 6) else (24.0 if 7 <= t.hour < 19 else 16.5))
    assert "warm_nights" not in kinds(one, latest_feed_age_days=1)
    assert "warm_nights" in kinds(two, latest_feed_age_days=1)


def test_dull_days_only_count_when_the_grow_light_is_off():
    dull = lambda start: series(start, 24 * 2, light=lambda t: 3000.0 if 8 <= t.hour < 16 else 0.0)
    assert "dull_days" in kinds(dull(APR), latest_feed_age_days=1)               # April: no grow light
    assert "dull_days" not in kinds(dull(JAN))                                   # January: the lamp tops it up, so it is not a gap


def test_bright_days_only_count_when_the_shade_is_off():
    bright = lambda start: series(start, 24 * 2, light=lambda t: 80000.0 if 6 <= t.hour < 18 else 0.0)
    assert "bright_days" in kinds(bright(APR), latest_feed_age_days=1)            # April: shade not in use yet
    assert "bright_days" not in kinds(bright(JUN), latest_feed_age_days=1)        # June: the shade is the answer


def test_dry_medium_needs_a_day_and_gives_season_specific_advice():
    rest = series(JAN, 24 * 3, moisture=2.0)
    s = [x for x in insights.detect(rest) if x["kind"] == "dry_medium"][0]
    assert "winter rest" in s["detail"] and "light extra watering" in s["action"]
    grow = [x for x in insights.detect(series(JUN, 24 * 3, moisture=2.0), latest_feed_age_days=1) if x["kind"] == "dry_medium"][0]
    assert "soak" in grow["action"]
    assert "dry_medium" not in kinds(series(JUN, 12, moisture=2.0), latest_feed_age_days=1)   # only half a day


def test_season_changeover_is_reported_when_the_window_contains_it():
    rows = series(datetime(2026, 2, 27, tzinfo=timezone.utc), 24 * 4)
    s = [x for x in insights.detect(rows) if x["kind"] == "season_change"]
    assert len(s) == 1 and "growing season begins" in s[0]["detail"]
    assert "season_change" not in kinds(series(JUN, 48), latest_feed_age_days=1)


def test_feeding_reminder_only_for_the_present_and_the_growing_season():
    rows = series(JUN, 24)
    assert "feed_due" in kinds(rows)                                              # never recorded
    assert "feed_due" in kinds(rows, latest_feed_age_days=10)
    assert "feed_due" not in kinds(rows, latest_feed_age_days=3)
    assert "feed_due" not in kinds(rows, is_current=False)                        # an old window: no reminder
    assert "feed_due" not in kinds(series(JAN, 24))                               # winter rest: no feeding


def test_most_serious_suggestions_come_first_and_the_text_uses_the_word_medium():
    rows = series(JUN, 24 * 3, temp=lambda t: 31.0 if 12 <= t.hour < 17 else 24.0, moisture=2.0)
    s = insights.detect(rows)
    assert [x["severity"] for x in s] == sorted((x["severity"] for x in s), key=insights.SEVERITY_ORDER.get)
    assert all("soil" not in (x["detail"] + x["action"] + x["title"]).lower() for x in s)
