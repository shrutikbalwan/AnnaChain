"""Every alert rule fires when it should and stays quiet when it should not,
including the one the deck leans on: a drifting sensor is called a suspect
sensor, not spoilage."""
from backend.alerts import DISAGREE_RUNS, EXCURSION_MIN

SHIP = {"min_c": 2.0, "max_c": 8.0}


def rec(seq, t, dev=1, **kw):
    r = {"device": dev, "seq": seq, "ts": 1790400000 + seq * 300, "temp_c": t,
         "c2h4_ppb": None, "tamper": False, "sensor_bad": False, "battery_pct": 90}
    r.update(kw)
    return r


def kinds(engine):
    return [a["kind"] for a in engine.fake.alerts]


def feed(engine, temps, dev=1, **kw):
    for i, t in enumerate(temps, 1):
        engine.on_record(rec(i, t, dev, **kw), SHIP)


def test_quiet_trip_raises_nothing(engine):
    feed(engine, [4.0] * 30)
    assert kinds(engine) == []


def test_short_excursion_is_not_an_alert(engine):
    feed(engine, [4.0, 9.0, 9.5] + [4.0] * 5)       # two readings above: 10 minutes
    assert "temp_high" not in kinds(engine)


def test_sustained_excursion_raises_once_then_clears(engine):
    feed(engine, [4.0] + [9.0] * (EXCURSION_MIN + 4) + [5.0])
    assert kinds(engine).count("temp_high") == 1
    assert kinds(engine)[-1] == "temp_back"


def test_too_cold_is_an_alert_too(engine):
    feed(engine, [1.0] * EXCURSION_MIN)
    assert kinds(engine) == ["temp_low"]


def test_sensor_fault_is_a_device_alert_not_a_temperature(engine):
    engine.on_record(rec(1, 0.0, sensor_bad=True), SHIP)
    assert kinds(engine) == ["sensor_fault"]


def test_tamper_and_low_battery(engine):
    engine.on_record(rec(1, 4.0, tamper=True, battery_pct=15), SHIP)
    assert set(kinds(engine)) == {"tamper", "battery"}


def test_ethylene_rise_against_this_trips_baseline(engine):
    for i in range(1, 13):
        engine.on_record(rec(i, 4.0, c2h4_ppb=20), SHIP)
    engine.on_record(rec(13, 4.0, c2h4_ppb=24), SHIP)   # +20%: not enough
    assert "ethylene" not in kinds(engine)
    engine.on_record(rec(14, 4.0, c2h4_ppb=30), SHIP)   # +50%
    assert "ethylene" in kinds(engine)


def test_ethylene_not_fitted_never_alerts(engine):
    feed(engine, [4.0] * 40, c2h4_ppb=None)
    assert "ethylene" not in kinds(engine)


def truck(engine, a, b, c, buckets):
    for k in range(buckets):
        engine.diagnose("T1", k * 300, {1: a, 2: b, 3: c})


def test_drifting_node_is_called_suspect(engine):
    truck(engine, 4.0, 4.1, 9.0, DISAGREE_RUNS)
    assert engine.suspect == {3}
    assert kinds(engine) == ["suspect_sensor"]


def test_disagreement_must_persist(engine):
    truck(engine, 4.0, 4.1, 9.0, DISAGREE_RUNS - 1)
    assert engine.suspect == set()


def test_honest_nodes_are_never_flagged(engine):
    truck(engine, 4.0, 4.3, 4.6, 20)
    assert engine.suspect == set()
    assert kinds(engine) == []


def test_a_shared_excursion_is_not_a_suspect_sensor(engine):
    """A door opened: all three warm together. That is the load, not a sensor."""
    truck(engine, 12.0, 12.4, 11.8, 10)
    assert engine.suspect == set()


def test_suspect_node_does_not_raise_temperature_alarms(engine):
    truck(engine, 4.0, 4.1, 9.0, DISAGREE_RUNS)
    feed(engine, [11.0] * 10, dev=3)
    assert "temp_high" not in kinds(engine)


def test_two_nodes_disagreeing_name_no_culprit(engine):
    for k in range(DISAGREE_RUNS):
        engine.diagnose("T2", k * 300, {1: 4.0, 2: 9.0})
    assert engine.suspect == set()
    assert kinds(engine) == ["disagree"]


# ── P3: a suspect node that agrees again is cleared ──────────────────────
def test_a_suspect_that_agrees_again_is_cleared(engine):
    """Clearing takes DISAGREE_RUNS agreeing buckets, as flagging takes
    DISAGREE_RUNS disagreeing ones: one good bucket is noise either way."""
    truck(engine, 4.0, 4.1, 9.0, DISAGREE_RUNS)
    assert engine.suspect == {3}
    for k in range(DISAGREE_RUNS - 1):
        engine.diagnose("T1", (DISAGREE_RUNS + k) * 300, {1: 4.0, 2: 4.1, 3: 4.2})
    assert engine.suspect == {3}                   # not yet
    engine.diagnose("T1", (2 * DISAGREE_RUNS) * 300, {1: 4.0, 2: 4.1, 3: 4.2})
    assert engine.suspect == set()
    assert kinds(engine) == ["suspect_sensor", "sensor_agrees"]
    assert engine.fake.suspects() == []            # the persisted row is gone too


def test_agreement_must_be_consecutive(engine):
    truck(engine, 4.0, 4.1, 9.0, DISAGREE_RUNS)
    t = DISAGREE_RUNS
    for k in range(DISAGREE_RUNS - 1):             # agrees, agrees...
        t += 1
        engine.diagnose("T1", t * 300, {1: 4.0, 2: 4.1, 3: 4.2})
    t += 1
    engine.diagnose("T1", t * 300, {1: 4.0, 2: 4.1, 3: 9.0})    # ...drifts again
    for k in range(DISAGREE_RUNS - 1):
        t += 1
        engine.diagnose("T1", t * 300, {1: 4.0, 2: 4.1, 3: 4.2})
    assert engine.suspect == {3}


def test_a_cleared_node_raises_temperature_alarms_again(engine):
    truck(engine, 4.0, 4.1, 9.0, DISAGREE_RUNS)
    for k in range(DISAGREE_RUNS):
        engine.diagnose("T1", (DISAGREE_RUNS + k) * 300, {1: 4.0, 2: 4.1, 3: 4.2})
    feed(engine, [11.0] * EXCURSION_MIN, dev=3)
    assert "temp_high" in kinds(engine)
