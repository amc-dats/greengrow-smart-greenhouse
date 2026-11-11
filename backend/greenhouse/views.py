from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from greengrow_common import topics

from . import assist, controller, environment, insights, rules
from .models import ActuatorState, Command, DeviceStatus, EnvState, Reading, ScheduleItem, Suggestion, WeatherSample
from .permissions import AdminOnly, SimToken
from .serializers import (
    ActuatorStateSerializer, CommandRequestSerializer, CommandSerializer, DeviceStatusSerializer,
    EnvStateSerializer, ExplainSerializer, FaultSerializer, ReadingSerializer, SimSampleSerializer,
    SimStepRequestSerializer, SuggestionSerializer, WeatherSampleSerializer,
)
from .weather import default_weather


class HealthView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["system"], auth=[], responses={200: dict})
    def get(self, request):
        connection.ensure_connection()
        return Response({"status": "ok"})


# ------------------------------------------------------------------ sense
class ReadingList(generics.ListAPIView):
    queryset = Reading.objects.all()
    serializer_class = ReadingSerializer

    @extend_schema(tags=["sense"], summary="Sensor readings, newest first")
    def get(self, request, *a, **kw):
        return super().get(request, *a, **kw)


class ReadingLatest(APIView):
    @extend_schema(tags=["sense"], summary="Latest sensor reading", responses=ReadingSerializer)
    def get(self, request):
        r = Reading.objects.first()
        if r is None:
            return Response({"detail": "no readings yet"}, status=404)
        return Response(ReadingSerializer(r).data)


class WeatherList(generics.ListAPIView):
    queryset = WeatherSample.objects.all()
    serializer_class = WeatherSampleSerializer

    @extend_schema(
        tags=["sense"],
        summary="Outside weather at each sample (weather input)",
        description="Real hourly weather for the configured location (Open-Meteo, CC BY 4.0), cached in the project.",
    )
    def get(self, request, *a, **kw):
        return super().get(request, *a, **kw)


# -------------------------------------------------------------------- act
class ActuatorList(APIView):
    @extend_schema(tags=["act"], summary="Current state of each actuator", responses=ActuatorStateSerializer(many=True))
    def get(self, request):
        known = {a.device: a for a in ActuatorState.objects.all()}
        out = []
        for d in topics.DEVICES:
            out.append(ActuatorStateSerializer(known[d]).data if d in known else {"device": d, "state": "unknown", "detail": {}, "updated_at": None})
        return Response(out)


class ActuatorCommand(APIView):
    @extend_schema(
        tags=["act"],
        summary="Queue a manual actuator command",
        description=(
            "Pump: `run` with `{duration_s}` or `stop`. Vent: `set` with `{position}` (0-100). "
            "Doser: `dose` with `{volume_ml}`. The command is queued, then checked by the safety rules "
            "and published over MQTT by the bridge. Poll `/commands/{id}/` for the outcome."
        ),
        request=CommandRequestSerializer,
        responses={202: CommandSerializer},
    )
    def post(self, request, device):
        if device not in topics.DEVICES:
            return Response({"detail": f"unknown device '{device}'"}, status=404)
        s = CommandRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        cmd = Command.objects.create(source="manual", device=device, action=s.validated_data["action"], params=s.validated_data["params"])
        return Response(CommandSerializer(cmd).data, status=status.HTTP_202_ACCEPTED)


class FeedRequestSerializer(serializers.Serializer):
    duration_s = serializers.IntegerField(min_value=1, max_value=120, help_text="watering run length in seconds")
    volume_ml = serializers.IntegerField(min_value=1, max_value=50, help_text="dilute fertiliser to dose into this watering, mL")


class Feed(APIView):
    @extend_schema(
        tags=["act"],
        summary="Feed the orchids: a watering with a fertiliser dose (manual)",
        description=(
            "Feeding is manual. This queues an irrigation run and a nutrient dose together, so the doser's interlock sees the pump "
            "running. Both still go through the safety rules: no dosing in the winter rest, daily and gap limits, and the dry-down "
            "gap between waterings. 'Weakly, weekly' is the usual rule: dilute, about every one to two weeks in the growing season."
        ),
        request=FeedRequestSerializer,
        responses={202: CommandSerializer(many=True)},
    )
    def post(self, request):
        s = FeedRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        pump = Command.objects.create(source="manual", device="pump", action="run", params={"duration_s": s.validated_data["duration_s"]})
        dose = Command.objects.create(source="manual", device="doser", action="dose", params={"volume_ml": s.validated_data["volume_ml"]})
        return Response(CommandSerializer([pump, dose], many=True).data, status=status.HTTP_202_ACCEPTED)


class CommandList(generics.ListAPIView):
    queryset = Command.objects.all()
    serializer_class = CommandSerializer

    @extend_schema(tags=["act"], summary="Command log with safety decisions")
    def get(self, request, *a, **kw):
        return super().get(request, *a, **kw)


class CommandDetail(generics.RetrieveAPIView):
    queryset = Command.objects.all()
    serializer_class = CommandSerializer

    @extend_schema(tags=["act"], summary="One command and its outcome")
    def get(self, request, *a, **kw):
        return super().get(request, *a, **kw)


class StatusView(APIView):
    @extend_schema(tags=["system"], summary="Online/offline status of the edge node and actuator node", responses=DeviceStatusSerializer(many=True))
    def get(self, request):
        return Response(DeviceStatusSerializer(DeviceStatus.objects.all(), many=True).data)


# ------------------------------------------------- simulator (data generator)
class SimStep(APIView):
    permission_classes = [SimToken]

    @extend_schema(
        tags=["generator"],
        summary="Advance the simulated greenhouse and return the sensor sample",
        description="Called by the edge node each sampling interval. Needs the `X-Sim-Token` header.",
        request=SimStepRequestSerializer,
        responses=SimSampleSerializer,
        parameters=[OpenApiParameter("X-Sim-Token", str, OpenApiParameter.HEADER, required=True)],
        auth=[],
    )
    def post(self, request):
        s = SimStepRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        minutes = s.validated_data.get("minutes", settings.SAMPLE_INTERVAL_MIN)
        wx = default_weather()
        with transaction.atomic():
            env = EnvState.get(lock=True)
            new = environment.advance(env.to_state(), minutes, wx)
            values = environment.sample(new, wx, fault=env.fault or None)
            out = environment.outside(new, wx)
            env.apply(new)
            env.sample_seq += 1
            env.save()
            WeatherSample.objects.create(sample_id=env.sample_seq, sim_ts=new.sim_time, temperature=round(out.temperature, 2),
                                         humidity=round(out.humidity, 1), solar=round(out.solar, 1))
        return Response({"sample_id": env.sample_seq, "sim_ts": new.sim_time, **values,
                         "outside_temperature": environment.outdoor_reading(new, wx)})


class SimState(APIView):
    @extend_schema(tags=["generator"], summary="Hidden true state of the simulated greenhouse (for debugging)", responses=EnvStateSerializer)
    def get(self, request):
        return Response(EnvStateSerializer(EnvState.get()).data)


class SimFault(APIView):
    permission_classes = [AdminOnly]

    @extend_schema(
        tags=["generator"],
        summary="Inject a sensor fault",
        description="Overrides one sensor's reported value (e.g. temperature=999) to test the plausibility and stale-data rules.",
        request=FaultSerializer,
        responses=FaultSerializer,
    )
    def post(self, request):
        s = FaultSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        env = EnvState.get()
        env.fault = s.validated_data
        env.save()
        return Response(s.validated_data)

    @extend_schema(tags=["generator"], summary="Clear the injected sensor fault", responses={204: None})
    def delete(self, request):
        env = EnvState.get()
        env.fault = {}
        env.save()
        return Response(status=204)


# ------------------------------------------------------------------ assist
class Explain(APIView):
    throttle_scope = "assist"

    @extend_schema(tags=["assist"], summary="Plain-language explanation of the current readings", responses=ExplainSerializer)
    def get(self, request):
        try:
            return Response(assist.explain())
        except assist.AssistError as exc:
            return Response({"detail": f"assist unavailable: {exc}"}, status=503)


class SuggestionList(generics.ListAPIView):
    queryset = Suggestion.objects.prefetch_related("items")
    serializer_class = SuggestionSerializer
    throttle_scope = "assist"

    @extend_schema(tags=["assist"], summary="List watering-schedule suggestions")
    def get(self, request, *a, **kw):
        return super().get(request, *a, **kw)

    @extend_schema(
        tags=["assist"],
        summary="Ask the assistant for a watering schedule",
        description="Claude proposes a schedule; it is checked by the safety rules and then waits for human approval.",
        request=None,
        responses={201: SuggestionSerializer},
    )
    def post(self, request):
        ctx = controller.build_context()
        try:
            result = assist.suggest()
        except assist.AssistError as exc:
            return Response({"detail": f"assist unavailable: {exc}"}, status=503)
        violations = assist.check_schedule(result["items"], ctx)
        sug = Suggestion.objects.create(
            mode=result["mode"], model=result["model"], explanation=result["explanation"],
            status="rejected_by_safety" if violations else "pending_review", violations=violations,
        )
        for it in result["items"]:
            ScheduleItem.objects.create(
                suggestion=sug, due_sim=ctx.now + timedelta(hours=it["at_offset_hours"]),
                duration_s=it["duration_s"], reason=it["reason"],
            )
        return Response(SuggestionSerializer(sug).data, status=201)


class SuggestionDetail(generics.RetrieveAPIView):
    queryset = Suggestion.objects.prefetch_related("items")
    serializer_class = SuggestionSerializer

    @extend_schema(tags=["assist"], summary="One suggestion")
    def get(self, request, *a, **kw):
        return super().get(request, *a, **kw)


class SuggestionApprove(APIView):
    @extend_schema(
        tags=["assist"],
        summary="Approve a suggestion (human confirmation)",
        description="Re-checks the schedule against the safety rules, then schedules it. Each run is checked again when it executes.",
        request=None,
        responses=SuggestionSerializer,
    )
    def post(self, request, pk):
        sug = get_object_or_404(Suggestion, pk=pk)
        if sug.status != "pending_review":
            return Response({"detail": f"suggestion is '{sug.status}', not pending review"}, status=409)
        ctx = controller.build_context()
        items = [{"due": i.due_sim, "duration_s": i.duration_s} for i in sug.items.all()]
        violations = rules.validate_schedule(items, ctx, allow_overdue=True)
        sug.status = "rejected_by_safety" if violations else "approved"
        sug.violations = violations
        sug.decided_at = timezone.now()
        sug.save()
        return Response(SuggestionSerializer(sug).data)


class SuggestionReject(APIView):
    @extend_schema(tags=["assist"], summary="Reject a suggestion", request=None, responses=SuggestionSerializer)
    def post(self, request, pk):
        sug = get_object_or_404(Suggestion, pk=pk)
        if sug.status != "pending_review":
            return Response({"detail": f"suggestion is '{sug.status}', not pending review"}, status=409)
        sug.status, sug.decided_at = "rejected", timezone.now()
        sug.save()
        return Response(SuggestionSerializer(sug).data)


# --------------------------------------------------------------- dashboard
class DashboardData(APIView):
    """Everything the console charts need in one call: the latest N simulated days, with the season's limits per point."""

    @extend_schema(
        tags=["sense"],
        summary="Chart data for the console dashboard (latest N simulated days)",
        parameters=[OpenApiParameter("days", int, description="simulated days to show, 1-400 (default 14)")],
        responses={200: dict},
    )
    def get(self, request):
        from collections import defaultdict
        from greengrow_common import safety_limits as L
        try:
            days = max(1, min(400, int(request.query_params.get("days", 14))))
        except ValueError:
            days = 14
        latest = Reading.objects.first()
        if latest is None:
            return Response({"detail": "no readings yet"}, status=404)
        since = latest.sim_ts - timedelta(days=days)
        rows = list(Reading.objects.filter(sim_ts__gt=since).order_by("sim_ts"))
        outside = {w.sample_id: w for w in WeatherSample.objects.filter(sim_ts__gt=since)}
        rest = lambda t: t.month in L.REST_MONTHS
        series = {k: [] for k in ("t", "temperature", "outside", "humidity", "outside_humidity", "soil", "light", "safety_min", "water_below", "soil_target")}
        daily = defaultdict(float)
        for r in rows:
            w = outside.get(r.sample_id)
            season = "rest" if rest(r.sim_ts) else "growing"
            series["t"].append(r.sim_ts.isoformat())
            series["temperature"].append(r.temperature)
            series["outside"].append(w.temperature if w else r.outside_temperature)
            series["humidity"].append(r.humidity)
            series["outside_humidity"].append(w.humidity if w else None)
            series["soil"].append(r.soil_moisture)
            series["light"].append(r.light)
            series["safety_min"].append(L.COLD_DANGER_C[season])
            series["water_below"].append(L.MOISTURE_AUTO_START[season])
            series["soil_target"].append(L.MOISTURE_TARGET[season])
            daily[r.sim_ts.date().isoformat()] += r.light / L.LUX_PER_UMOL * 1800 / 1e6
        now_season = "rest" if rest(latest.sim_ts) else "growing"
        fed = Command.objects.filter(device="doser", action="dose", status="accepted", sim_ts__isnull=False).order_by("-sim_ts").first()
        feed_age = (latest.sim_ts - fed.sim_ts).total_seconds() / 86400 if fed else None
        samples = [{"t": r.sim_ts, "temp": r.temperature, "outside": (outside[r.sample_id].temperature if r.sample_id in outside else r.outside_temperature),
                    "rh": r.humidity, "moisture": r.soil_moisture, "light": r.light} for r in rows]
        return Response({
            "suggestions": insights.detect(samples, latest_feed_age_days=feed_age),
            "sim_now": latest.sim_ts.isoformat(), "days": days, "season": now_season,
            "series": series,
            "daily": {"days": list(daily), "dli": [round(v, 1) for v in daily.values()]},
            "limits": {"vent_open_c": L.VENT_OPEN_TEMP_C, "humidity_band": list(L.HUMIDITY_BAND), "dli_band": list(L.DLI_BAND), "peak_lux_max": L.PEAK_LUX_MAX,
                       "day_band_c": list(rules.day_band(latest.sim_ts)), "night_band_c": list(L.NIGHT_BAND_C[now_season])},
        })
