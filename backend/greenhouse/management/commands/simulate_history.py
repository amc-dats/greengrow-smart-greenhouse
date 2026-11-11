"""Fill the database with simulated history, without waiting for it: runs the greenhouse model and the rules-based controller over a
stretch of real weather (offline, no MQTT) and stores the readings and outside weather. Existing readings are never touched: only
times outside the span already stored are added. With --resume-live the live simulator is moved to the end of the history, so the
running stack carries on from there. Stop the edge node and the bridge first so nothing is written meanwhile."""
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from greenhouse import closedloop
from greenhouse.models import EnvState, Reading, WeatherSample
from greenhouse.weather import WeatherData


class Command(BaseCommand):
    help = "Simulate days of history on real weather and store it (never overwrites existing readings)."

    def add_arguments(self, parser):
        parser.add_argument("--weather", default=settings.WEATHER_FILE, help="weather JSON (default: the configured file)")
        parser.add_argument("--start", help="ISO start time (default: first hour of the weather file)")
        parser.add_argument("--days", type=float, default=365)
        parser.add_argument("--seed", type=int, default=1)
        parser.add_argument("--resume-live", action="store_true", help="move the live simulator to the end of the history")
        parser.add_argument("--dry-run", action="store_true", help="simulate and report, store nothing")

    def handle(self, weather, start, days, seed, resume_live, dry_run, **_):
        w = WeatherData.load(weather)
        t0 = datetime.fromisoformat(start) if start else w.start
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        steps = int(days * 24 * 2) - 1                     # the last sample lands on 23:30 of the final day
        self.stdout.write(f"simulating {steps} samples from {t0:%Y-%m-%d %H:%M} on {weather} ...")
        res = closedloop.run(w, t0, days=steps / 48, noise=True, seed=seed)
        rows = res["rows"]
        self.stdout.write(f"done: {len(rows)} samples, {res['accepted']} commands accepted, {res['rejected']} refused by the rules")

        first = Reading.objects.order_by("sim_ts").first()
        last = Reading.objects.order_by("-sim_ts").first()
        lo, hi = (first.sim_ts, last.sim_ts) if first else (None, None)
        keep = [r for r in rows if lo is None or r["t"] < lo or r["t"] > hi]
        self.stdout.write(f"existing readings span {lo} to {hi}; adding {len(keep)} of {len(rows)} samples (none inside that span)")
        self.summary(rows)
        if dry_run:
            return
        with transaction.atomic():
            next_id = (Reading.objects.order_by("-sample_id").values_list("sample_id", flat=True).first() or 0) + 1
            readings, weathers = [], []
            for i, r in enumerate(keep):
                sid = next_id + i
                readings.append(Reading(sim_ts=r["t"], sample_id=sid, soil_moisture=r["moisture"], temperature=r["temp"], humidity=r["rh"],
                                        light=r["light"], outside_temperature=r["out_temp"]))
                weathers.append(WeatherSample(sample_id=sid, sim_ts=r["t"], temperature=r["out_temp"], humidity=r["out_rh"], solar=r["out_solar"]))
            Reading.objects.bulk_create(readings, batch_size=2000)
            WeatherSample.objects.bulk_create(weathers, batch_size=2000)
            if resume_live:
                s, env = res["state"], EnvState.get(lock=True)
                for f in EnvState.FIELDS:
                    if hasattr(s, f):
                        setattr(env, f, getattr(s, f))
                env.pending_pump_s = env.pending_dose_ml = env.pending_heater_s = env.pending_humidifier_s = env.pending_lamp_s = 0.0
                env.sample_seq = next_id + len(keep) - 1
                env.save()
                self.stdout.write(f"live simulator moved to {env.sim_time}")
        self.stdout.write(self.style.SUCCESS(f"stored {len(readings)} readings and weather samples"))

    def summary(self, rows):
        by = defaultdict(list)
        for r in rows:
            by[r["t"].strftime("%Y-%m")].append(r)
        self.stdout.write("month    outside  inside  min   max  heater h/day  lamp h/day  shade h/day  soil %")
        for m, rs in sorted(by.items()):
            d = len(rs) / 48
            self.stdout.write(f"{m}  {st.mean(r['out_temp'] for r in rs):6.1f} {st.mean(r['temp'] for r in rs):7.1f} {min(r['temp'] for r in rs):5.1f} {max(r['temp'] for r in rs):5.1f} "
                              f"{sum(1 for r in rs if r['heater_s']) / 2 / d:11.1f} {sum(1 for r in rs if r['lamp_s']) / 2 / d:11.1f} "
                              f"{sum(1 for r in rs if r['shade'] > 50) / 2 / d:11.1f} {st.mean(r['moisture'] for r in rs):9.0f}")
