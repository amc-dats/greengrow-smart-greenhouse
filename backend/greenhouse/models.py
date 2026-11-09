from django.conf import settings
from django.db import models
from django.utils import timezone

from . import environment


class Reading(models.Model):
    """One telemetry sample received over MQTT (all four sensors)."""

    ts = models.DateTimeField(default=timezone.now, db_index=True, help_text="wall-clock receive time")
    sim_ts = models.DateTimeField(db_index=True, help_text="simulated greenhouse time")
    sample_id = models.IntegerField(db_index=True)
    soil_moisture = models.FloatField(help_text="%")
    temperature = models.FloatField(help_text="degrees C")
    humidity = models.FloatField(help_text="% relative humidity")
    light = models.FloatField(help_text="lux")
    plausible = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]


class ActuatorState(models.Model):
    """Latest state reported by each actuator over MQTT."""

    device = models.CharField(max_length=20, unique=True)
    state = models.CharField(max_length=20, default="unknown")
    detail = models.JSONField(default=dict)
    last_applied = models.CharField(max_length=40, blank=True, default="", help_text="'cmd_id:event' already applied")
    updated_at = models.DateTimeField(auto_now=True)


class DeviceStatus(models.Model):
    """Online/offline status from MQTT Last Will messages (edge node, actuator node)."""

    name = models.CharField(max_length=40, unique=True)
    online = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class Command(models.Model):
    """Every actuator command, whatever its source, with the safety decision and reasons."""

    SOURCES = [("auto", "auto"), ("manual", "manual"), ("schedule", "schedule")]
    STATUSES = [("queued", "queued"), ("accepted", "accepted"), ("rejected", "rejected")]

    created_at = models.DateTimeField(default=timezone.now)
    sim_ts = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=SOURCES)
    device = models.CharField(max_length=20)
    action = models.CharField(max_length=20)
    params = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUSES, default="queued", db_index=True)
    reasons = models.JSONField(default=list)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]


class Suggestion(models.Model):
    """An assist output: explanation plus proposed watering schedule, subject to safety check and human approval."""

    STATUSES = [
        ("pending_review", "pending_review"),
        ("approved", "approved"),
        ("rejected", "rejected"),
        ("rejected_by_safety", "rejected_by_safety"),
    ]
    created_at = models.DateTimeField(default=timezone.now)
    mode = models.CharField(max_length=10)  # claude | mock
    model = models.CharField(max_length=60, blank=True)
    explanation = models.TextField()
    status = models.CharField(max_length=20, choices=STATUSES, default="pending_review")
    violations = models.JSONField(default=list)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]


class ScheduleItem(models.Model):
    STATUSES = [("pending", "pending"), ("submitted", "submitted")]
    suggestion = models.ForeignKey(Suggestion, related_name="items", on_delete=models.CASCADE)
    due_sim = models.DateTimeField()
    duration_s = models.IntegerField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="pending")
    command = models.ForeignKey(Command, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["due_sim"]


class WeatherSample(models.Model):
    """Outside weather (from the cached weather input) at the time of each sensor sample, for the dashboard."""

    sample_id = models.IntegerField(db_index=True)
    sim_ts = models.DateTimeField(db_index=True)
    temperature = models.FloatField(help_text="outside air, degrees C")
    humidity = models.FloatField(help_text="outside relative humidity, %")
    solar = models.FloatField(help_text="outside solar radiation, W/m2")

    class Meta:
        ordering = ["-id"]


class EnvState(models.Model):
    """Singleton: the hidden true state of the simulated greenhouse."""

    sim_time = models.DateTimeField()
    soil_moisture = models.FloatField(default=55.0)
    temperature = models.FloatField(default=20.0)
    mass_temp = models.FloatField(default=20.0)
    vapour_hpa = models.FloatField(default=14.0)
    vent_pos = models.FloatField(default=0.0)
    pending_pump_s = models.FloatField(default=0.0)
    pending_dose_ml = models.FloatField(default=0.0)
    pending_heater_s = models.FloatField(default=0.0)
    pending_humidifier_s = models.FloatField(default=0.0)
    nutrient_ml_total = models.FloatField(default=0.0)
    sample_seq = models.IntegerField(default=0)
    fault = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    FIELDS = [f.name for f in environment.SimState.__dataclass_fields__.values()]

    @classmethod
    def get(cls, lock: bool = False) -> "EnvState":
        qs = cls.objects.select_for_update() if lock else cls.objects
        obj = qs.filter(pk=1).first()
        if obj is None:
            from .weather import default_weather

            wx = default_weather()
            start = settings.SIM_START or getattr(wx, "start", None) or timezone.now()
            init = environment.initial_state(start, wx)
            obj, _ = cls.objects.get_or_create(pk=1, defaults={name: getattr(init, name) for name in cls.FIELDS})
            if lock:
                obj = cls.objects.select_for_update().get(pk=1)
        return obj

    def to_state(self) -> environment.SimState:
        return environment.SimState(**{name: getattr(self, name) for name in self.FIELDS})

    def apply(self, state: environment.SimState) -> None:
        for name in self.FIELDS:
            setattr(self, name, getattr(state, name))
