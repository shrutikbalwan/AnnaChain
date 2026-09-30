"""Q10 shelf life against values worked out by hand.

Table grapes: 60 days at 0 C, Q10 = 2.5, so
    life(0 C)  = 60 days     -> one day consumes 1/60 = 1.667 %
    life(10 C) = 60 / 2.5    = 24 days -> one day consumes 1/24 = 4.167 %
"""
from backend import shelflife

DAY = 86400


def flat(t, days=1):
    return [{"ts": 0, "t": t}, {"ts": days * DAY, "t": t}]


def test_one_day_at_the_reference_temperature():
    e = shelflife.shelf_life(flat(0.0), "table grapes", 2.0, 8.0)
    assert e["consumed_pct"] == 1.7
    assert e["remaining_pct"] == 98.3
    assert e["remaining_days"] == 59.0


def test_one_day_ten_degrees_warmer_costs_two_and_a_half_times_as_much():
    e = shelflife.shelf_life(flat(10.0), "table grapes", 2.0, 8.0)
    assert e["consumed_pct"] == 4.2
    assert e["remaining_pct"] == 95.8
    assert e["remaining_days"] == 57.5


def test_a_sensor_fault_is_skipped_not_read_as_zero():
    s = [{"ts": 0, "t": 10.0}, {"ts": DAY // 2, "t": None}, {"ts": DAY, "t": 10.0}]
    assert shelflife.shelf_life(s, "table grapes")["remaining_pct"] == 95.8


def test_agreed_range_and_commodity_ideal_are_kept_apart():
    e = shelflife.shelf_life(flat(5.0), "table grapes", 2.0, 8.0)
    assert e["agreed_range_c"] == [2.0, 8.0]
    assert e["commodity_ideal_c"] == [-0.5, 2.0]
    assert e["agreed_is_warmer_than_ideal"] is True
    # 5 C is inside what was agreed, so no minutes are counted against it...
    assert e["minutes_above_agreed"] == 0
    # ...even though it is warmer than the crop would like, and costs life.
    assert e["remaining_pct"] < 98.3


def test_excursion_minutes_count_against_the_agreed_range():
    e = shelflife.shelf_life(flat(9.0), "table grapes", 2.0, 8.0)
    assert e["minutes_above_agreed"] == 1440


def test_every_estimate_carries_the_caveat():
    e = shelflife.shelf_life(flat(4.0), "table grapes")
    assert "NOT been validated" in e["caveat"]


def test_classify_door_opening_versus_cooling_failure():
    base = [{"ts": i * 300, "t": 4.0} for i in range(20)]
    door = base[:10] + [{"ts": 3000, "t": 12.0}, {"ts": 3300, "t": 9.0}] + \
           [{"ts": 3600 + i * 300, "t": 4.0} for i in range(8)]
    assert shelflife.classify(door, "table grapes", agreed_hi=8.0)["kind"] == "door opening"
    failing = base[:10] + [{"ts": 3000 + i * 300, "t": 9.0 + i * 0.3} for i in range(10)]
    assert shelflife.classify(failing, "table grapes", agreed_hi=8.0)["kind"] == "cooling failure"
    assert shelflife.classify(base, "table grapes", peer_disagreement=True)["kind"] == \
        "suspect sensor"
