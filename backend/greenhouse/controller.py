"""Glue between the database, the rules engine and MQTT.

The bridge process is the ONLY publisher of actuator commands. Every command, from any source, is
recorded in the Command table and checked by rules.check_command() before it is published.
Publishing is injected as a callable so this module can be tested without a broker.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Callable

from django.conf import settings
from django.utils import timezone

from greengrow_common import topics

from . import rules
from .models import Command, EnvState, Reading, ScheduleItem

log = logging.getLogger("greengrow.controller")
Publisher = Callable[[str, dict], bool]


def build_context() -> rules.Context:
    env = EnvState.get()
    rd = Reading.objects.order_by("-id").first()
    readings = None
    age = None
    if rd:
        readings = {"soil_moisture": rd.soil_moisture, "temperature": rd.temperature, "humidity": rd.humidity, "light": rd.light}
        age = (timezone.now() - rd.ts).total_seconds()
    history = [
        rules.HistoryItem(c.device, c.action, c.params, c.sim_ts)
        for c in Command.objects.filter(status="accepted", sim_ts__gt=env.sim_time - timedelta(hours=48))
    ]
    return rules.Context(
        now=env.sim_time,
        readings=readings,
        reading_age_s=age,
        max_age_s=settings.MAX_READING_AGE_S,
        history=history,
        vent_pos=env.vent_pos,
    )


def submit(cmd: Command, ctx: rules.Context, publish: Publisher) -> bool:
    """Safety-check a queued command and, if allowed, publish it. Returns True if published."""
    cmd.sim_ts = ctx.now
    cmd.decided_at = timezone.now()
    reasons = rules.check_command({"device": cmd.device, "action": cmd.action, "params": cmd.params}, ctx)
    if reasons:
        cmd.status, cmd.reasons = "rejected", reasons
        cmd.save()
        log.info("command %s REJECTED (%s %s): %s", cmd.id, cmd.device, cmd.action, "; ".join(reasons))
        return False
    payload = {"cmd_id": cmd.id, "action": cmd.action, **cmd.params, "issued_at": time.time(), "sim_ts": ctx.now.isoformat()}
    if not publish(topics.cmd(settings.ZONE, cmd.device), payload):
        # Fail safe: if the broker cannot take the command it is refused, never silently queued.
        cmd.status, cmd.reasons = "rejected", ["could not publish to the MQTT broker"]
        cmd.save()
        return False
    cmd.status, cmd.reasons = "accepted", []
    cmd.save()
    ctx.history.append(rules.HistoryItem(cmd.device, cmd.action, cmd.params, ctx.now))
    if cmd.device == "vent":
        ctx.vent_pos = float(cmd.params["position"])  # later commands in this pass see the new position
    log.info("command %s ACCEPTED (%s %s %s, source=%s)", cmd.id, cmd.device, cmd.action, cmd.params, cmd.source)
    return True


def dispatch_queued(publish: Publisher) -> int:
    queued = list(Command.objects.filter(status="queued").order_by("id"))
    if not queued:
        return 0
    ctx = build_context()
    for cmd in queued:
        submit(cmd, ctx, publish)
    return len(queued)


def run_auto(publish: Publisher) -> int:
    if not settings.AUTO_CONTROL:
        return 0
    ctx = build_context()
    proposals = rules.auto_decide(ctx)
    for p in proposals:
        cmd = Command.objects.create(source="auto", device=p["device"], action=p["action"], params=p["params"])
        submit(cmd, ctx, publish)
    return len(proposals)


def run_schedule(publish: Publisher) -> int:
    """Execute approved assist schedule items that have fallen due (re-checked by the rules at run time)."""
    env = EnvState.get()
    due = list(
        ScheduleItem.objects.filter(suggestion__status="approved", status="pending", due_sim__lte=env.sim_time).order_by("due_sim")
    )
    if not due:
        return 0
    ctx = build_context()
    for item in due:
        cmd = Command.objects.create(source="schedule", device="pump", action="run", params={"duration_s": item.duration_s})
        submit(cmd, ctx, publish)
        item.status, item.command = "submitted", cmd
        item.save()
    return len(due)
