import pytest

import edge_node as e


def test_real_interval_follows_design_and_demo_speed():
    assert e.real_interval_s(30, 1) == 1800      # design value: one sample per 30 minutes
    assert e.real_interval_s(30, 180) == 10      # demo: 30 simulated minutes in 10 s
    with pytest.raises(ValueError):
        e.real_interval_s(0, 1)
    with pytest.raises(ValueError):
        e.real_interval_s(30, 0)


def test_payload_is_one_atomic_snapshot_with_units():
    sample = {"sample_id": 4, "sim_ts": "2026-06-01T06:30:00Z", "soil_moisture": 50.1, "temperature": 18.2,
              "humidity": 70.0, "light": 120.0, "extra": "ignored"}
    p = e.build_payload(sample)
    assert set(p) == {"sample_id", "sim_ts", "soil_moisture", "temperature", "humidity", "light", "units"}
    assert p["units"]["temperature"] == "C"


def test_incomplete_generator_response_is_refused_not_invented():
    with pytest.raises(ValueError):
        e.build_payload({"sample_id": 1, "sim_ts": "x", "soil_moisture": 1})
