import actuator_node as a


class FakeTimer:
    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args, self.daemon, self.cancelled = interval, function, args, False, False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


class Rig:
    def __init__(self, speed=1.0):
        self.out, self.t = [], 1000.0
        self.timers = []

        def make_timer(*args, **kw):
            tm = FakeTimer(*args, **kw)
            self.timers.append(tm)
            return tm

        self.node = a.ActuatorNode(lambda d, p: self.out.append((d, p)), sim_speed=speed, now=lambda: self.t, timer_cls=make_timer)

    def cmd(self, device, cmd_id=1, issued_at=None, **kw):
        self.node.handle_command(device, {"cmd_id": cmd_id, "issued_at": self.t if issued_at is None else issued_at, **kw})

    def last(self):
        return self.out[-1][1]


def test_hard_check_rejects_out_of_range_and_malformed():
    assert a.hard_check("pump", {"action": "run", "duration_s": 120}) is None
    assert a.hard_check("pump", {"action": "run", "duration_s": 121})
    assert a.hard_check("pump", {"action": "run", "duration_s": 1.5})
    assert a.hard_check("pump", {"action": "run", "duration_s": True})
    assert a.hard_check("pump", {"action": "stop"}) is None
    assert a.hard_check("vent", {"action": "set", "position": 101})
    assert a.hard_check("doser", {"action": "dose", "volume_ml": 51})
    assert a.hard_check("heater", {"action": "on"})


def test_pump_run_reports_start_and_completion():
    r = Rig(speed=180)
    r.cmd("pump", action="run", duration_s=90)
    assert r.last()["event"] == "run_started" and r.timers[0].interval == 2.0   # minimum real run time
    r.timers[0].fire()
    assert r.last()["event"] == "run_complete" and r.last()["ran_s"] == 90 and r.last()["state"] == "idle"


def test_pump_cannot_be_started_twice():
    r = Rig()
    r.cmd("pump", 1, action="run", duration_s=60)
    r.cmd("pump", 2, action="run", duration_s=60)
    assert r.last()["event"] == "rejected" and "already running" in r.last()["reason"]


def test_heater_and_humidifier_can_be_retriggered_but_the_pump_cannot():
    for device in ("heater", "humidifier"):
        r = Rig(speed=1)
        r.cmd(device, 1, action="run", duration_s=1800)
        r.t += 1790                                              # the next command arrives just before the run ends
        r.cmd(device, 2, action="run", duration_s=900)
        events = [(p["event"], p["cmd_id"]) for d, p in r.out if d == device]
        assert events == [("run_started", 1), ("stopped", 1), ("run_started", 2)]
        assert r.out[1][1]["ran_s"] == 1790 and r.timers[0].cancelled      # the first run is closed with what it delivered
    p = Rig(speed=1)
    p.cmd("pump", 1, action="run", duration_s=100)
    p.cmd("pump", 2, action="run", duration_s=100)
    assert p.last()["event"] == "rejected"


def test_over_limit_command_rejected_by_actuator_even_if_backend_allowed_it():
    r = Rig()
    r.cmd("pump", action="run", duration_s=500)
    assert r.last()["event"] == "rejected" and r.timers == []
    r.cmd("doser", 2, action="dose", volume_ml=500)
    assert r.last()["event"] == "rejected"


def test_stale_command_is_dropped():
    r = Rig()
    r.cmd("pump", action="run", duration_s=30, issued_at=r.t - 600)
    assert r.last()["event"] == "rejected" and "expired" in r.last()["reason"]
    r.cmd("pump", 2, action="run", duration_s=30, issued_at=r.t + 3600)       # clock far in the future
    assert r.last()["event"] == "rejected"


def test_doser_interlocked_to_pump():
    r = Rig()
    r.cmd("doser", 1, action="dose", volume_ml=20)
    assert r.last()["event"] == "rejected" and "interlock" in r.last()["reason"]
    r.cmd("pump", 2, action="run", duration_s=60)
    r.cmd("doser", 3, action="dose", volume_ml=20)
    assert r.last()["event"] == "dose_complete" and r.last()["dosed_ml"] == 20


def test_heater_runs_for_its_duration_and_has_its_own_limit():
    r = Rig(speed=180)
    r.cmd("heater", 1, action="run", duration_s=900)
    assert r.last()["event"] == "run_started" and r.last()["device"] == "heater"
    r.timers[0].fire()
    assert r.last()["event"] == "run_complete" and r.last()["ran_s"] == 900
    r.cmd("heater", 2, action="run", duration_s=1801)
    assert r.last()["event"] == "rejected"
    assert a.hard_check("pump", {"action": "run", "duration_s": 1800})      # pump limit (120 s) is separate


def test_humidifier_runs_for_its_duration_and_has_its_own_limit():
    r = Rig(speed=180)
    r.cmd("humidifier", 1, action="run", duration_s=1200)
    assert r.last()["event"] == "run_started" and r.last()["device"] == "humidifier"
    r.timers[0].fire()
    assert r.last()["event"] == "run_complete" and r.last()["ran_s"] == 1200
    r.cmd("humidifier", 2, action="run", duration_s=1801)
    assert r.last()["event"] == "rejected"


def test_failsafe_stops_pump_heater_and_humidifier():
    r = Rig(speed=1)
    r.cmd("pump", 1, action="run", duration_s=100)
    r.cmd("heater", 2, action="run", duration_s=1000)
    r.cmd("humidifier", 3, action="run", duration_s=1000)
    r.t += 10
    r.node.failsafe("broker disconnected")
    stopped = {d: p for d, p in r.out if p["event"] == "stopped"}
    assert set(stopped) == {"pump", "heater", "humidifier"} and stopped["heater"]["ran_s"] == 10


def test_vent_moves_and_reports_position():
    r = Rig()
    r.cmd("vent", action="set", position=60)
    assert r.last() == {**r.last(), "event": "moved", "position": 60, "state": "open"}
    r.cmd("vent", 2, action="set", position=0)
    assert r.last()["state"] == "closed"


def test_failsafe_stops_pump_and_reports_partial_run():
    r = Rig(speed=10)
    r.cmd("pump", action="run", duration_s=100)
    r.t += 3.0                                   # 3 real seconds = 30 simulated seconds
    r.node.failsafe("broker disconnected")
    assert r.last()["event"] == "stopped" and r.last()["ran_s"] == 30 and r.timers[0].cancelled
    r.node.failsafe("again")                     # nothing running: no second event
    assert r.out[-1][1]["event"] == "stopped" and len(r.out) == 2


def test_stop_command_ends_a_run_early():
    r = Rig(speed=1)
    r.cmd("pump", 1, action="run", duration_s=100)
    r.t += 40
    r.cmd("pump", 2, action="stop")
    assert r.last()["event"] == "stopped" and r.last()["ran_s"] == 40
