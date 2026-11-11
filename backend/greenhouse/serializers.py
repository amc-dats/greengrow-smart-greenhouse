from rest_framework import serializers

from .models import ActuatorState, Command, DeviceStatus, EnvState, Reading, ScheduleItem, Suggestion, WeatherSample


class ReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reading
        fields = ["id", "ts", "sim_ts", "sample_id", "soil_moisture", "temperature", "humidity", "light", "outside_temperature", "plausible"]


class WeatherSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeatherSample
        fields = ["id", "sample_id", "sim_ts", "temperature", "humidity", "solar"]


class ActuatorStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActuatorState
        fields = ["device", "state", "detail", "updated_at"]


class DeviceStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceStatus
        fields = ["name", "online", "updated_at"]


class CommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Command
        fields = ["id", "created_at", "sim_ts", "source", "device", "action", "params", "status", "reasons", "decided_at"]


class CommandRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["run", "stop", "set", "dose"])
    params = serializers.JSONField(required=False, default=dict)


class ScheduleItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleItem
        fields = ["id", "due_sim", "duration_s", "reason", "status"]


class SuggestionSerializer(serializers.ModelSerializer):
    items = ScheduleItemSerializer(many=True, read_only=True)

    class Meta:
        model = Suggestion
        fields = ["id", "created_at", "mode", "model", "explanation", "status", "violations", "decided_at", "items"]


class ExplainSerializer(serializers.Serializer):
    mode = serializers.CharField()
    model = serializers.CharField(allow_blank=True)
    explanation = serializers.CharField()
    snapshot = serializers.JSONField()


class SimStepRequestSerializer(serializers.Serializer):
    minutes = serializers.FloatField(required=False, min_value=0.1, max_value=1440)


class SimSampleSerializer(serializers.Serializer):
    sample_id = serializers.IntegerField()
    sim_ts = serializers.DateTimeField()
    soil_moisture = serializers.FloatField()
    temperature = serializers.FloatField()
    humidity = serializers.FloatField()
    light = serializers.FloatField()
    outside_temperature = serializers.FloatField(help_text="outdoor temperature sensor, degrees C")


class FaultSerializer(serializers.Serializer):
    metric = serializers.ChoiceField(choices=["soil_moisture", "temperature", "humidity", "light"])
    value = serializers.FloatField()

    def validate(self, data):
        # Keep injected temperatures within a sane test range (the safety rules already treat >60 C as implausible).
        if data["metric"] == "temperature" and not -100 <= data["value"] <= 100:
            raise serializers.ValidationError({"value": "temperature faults must be between -100 and 100 C"})
        return data


class EnvStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvState
        exclude = ["id"]
