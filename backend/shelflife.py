"""AnnaChain — shelf life and anomaly classification.

There is a version of this file that uses a neural network, reports 94%
accuracy, and falls apart the moment a judge asks what it was validated
against. We are not writing that one.

**Shelf life** is computed from Q10 kinetics, which is the standard model for
temperature-dependent deterioration and has been for decades:

    life(T) = life_ref x Q10 ^ ((T_ref - T) / 10)

Each reading consumes a fraction of the remaining life equal to the time it
covers divided by the life at that temperature. Warm stretches consume it
faster — which is precisely why a 25-minute excursion matters and why a
logger that missed it is worse than useless.

The parameters below are literature-typical values for each commodity. **We
have not validated them ourselves**, and the API says so on every response.
Validating one commodity against real storage trials is a named next step, not
a thing to quietly imply is done.

**Anomaly classification** is rules over the shape of the curve, not a
classifier. A door opening, a compressor failing and a sensor drifting look
different from each other — fast rise then fast recovery, slow steady rise that
never recovers, and a divergence from the other nodes on the same truck. Each
verdict here can be explained in one sentence to the person who has to act on
it, which a softmax output cannot.
"""
from dataclasses import dataclass


@dataclass
class Kinetics:
    commodity: str
    life_days_ref: float      # shelf life at the reference temperature
    t_ref_c: float            # that reference temperature
    q10: float                # rate multiplier per 10 C
    ideal_lo: float
    ideal_hi: float
    source: str


# Literature-typical values. Not our measurements — see the note above.
COMMODITIES = {
    "table grapes": Kinetics("table grapes", 60, 0.0, 2.5, -0.5, 2.0,
                             "typical published cold-storage life; not validated here"),
    "mango":        Kinetics("mango", 21, 12.0, 2.8, 10.0, 13.0,
                             "chilling-sensitive below ~10 C; not validated here"),
    "banana":       Kinetics("banana", 21, 13.5, 3.0, 13.0, 15.0,
                             "chilling injury below ~13 C; not validated here"),
    "tomato":       Kinetics("tomato", 14, 12.0, 2.5, 10.0, 13.0,
                             "not validated here"),
    "leafy greens": Kinetics("leafy greens", 14, 0.0, 2.8, 0.0, 2.0,
                             "not validated here"),
    "default":      Kinetics("produce", 30, 4.0, 2.5, 2.0, 8.0,
                             "generic produce placeholder; not validated here"),
}


def kinetics_for(commodity: str) -> Kinetics:
    if not commodity:
        return COMMODITIES["default"]
    return COMMODITIES.get(commodity.strip().lower(), COMMODITIES["default"])


def shelf_life(series, commodity: str, agreed_lo=None, agreed_hi=None) -> dict:
    """series: [{ts, t}] oldest first. t may be None for a flagged sensor fault.

    Two different temperature ranges are in play, and conflating them is the
    easiest way to produce nonsense:

      * the **agreed** range — what the shipper contracted to hold. Breaching it
        is a commercial fact, and it is what alerts fire on.
      * the commodity's **ideal** range — what the crop would prefer. Often
        colder than what was agreed.

    Deterioration is computed from the physics either way. Excursions are
    counted against the agreed range, because that is the one someone signed.
    """
    k = kinetics_for(commodity)
    lo = k.ideal_lo if agreed_lo is None else agreed_lo
    hi = k.ideal_hi if agreed_hi is None else agreed_hi
    pts = [p for p in series if p.get("t") is not None]
    if len(pts) < 2:
        return {"ok": False, "reason": "not enough readings"}

    consumed = 0.0
    chilled_minutes = 0.0
    warm_minutes = 0.0
    worst = None

    for a, b in zip(pts, pts[1:]):
        dt_days = max(0.0, (b["ts"] - a["ts"]) / 86400.0)
        if dt_days <= 0:
            continue
        t = (a["t"] + b["t"]) / 2.0

        # life at this temperature, clamped so a freak reading cannot make the
        # estimate meaningless
        life = k.life_days_ref * (k.q10 ** ((k.t_ref_c - t) / 10.0))
        life = max(0.25, min(life, k.life_days_ref * 8))
        consumed += dt_days / life

        if t < lo:
            chilled_minutes += dt_days * 1440
        elif t > hi:
            warm_minutes += dt_days * 1440
            if worst is None or t > worst:
                worst = t

    remaining = max(0.0, 1.0 - consumed)
    elapsed_days = (pts[-1]["ts"] - pts[0]["ts"]) / 86400.0

    # What it would have been had the whole journey been held at the ideal
    # temperature. The difference between the two is the cost of the excursions,
    # in days, which is the number a shipper can act on.
    ideal_life = k.life_days_ref * (k.q10 ** ((k.t_ref_c - hi) / 10.0))
    ideal_consumed = elapsed_days / max(ideal_life, 0.25)
    lost_days = max(0.0, (consumed - ideal_consumed)) * ideal_life

    return {
        "ok": True,
        "commodity": k.commodity,
        "remaining_pct": round(remaining * 100, 1),
        "remaining_days": round(remaining * k.life_days_ref, 1),
        "consumed_pct": round(consumed * 100, 1),
        "journey_days": round(elapsed_days, 2),
        "days_lost_to_warmth": round(lost_days, 2),
        "agreed_range_c": [lo, hi],
        "commodity_ideal_c": [k.ideal_lo, k.ideal_hi],
        "agreed_is_warmer_than_ideal": hi > k.ideal_hi,
        "minutes_above_agreed": int(warm_minutes),
        "minutes_below_agreed": int(chilled_minutes),
        "warmest_c": round(worst, 2) if worst is not None else None,
        "model": f"Q10 = {k.q10}, reference {k.life_days_ref} days at {k.t_ref_c} C",
        "caveat": "Kinetics parameters are literature-typical and have NOT been "
                  "validated against storage trials by this project. Treat the "
                  "figure as indicative, not as a guarantee.",
        "source": k.source,
    }


def classify(series, commodity: str, peer_disagreement: bool = False,
             agreed_hi=None) -> dict:
    """Name what happened, in terms a driver or a dock supervisor can act on."""
    k = kinetics_for(commodity)
    hi = k.ideal_hi if agreed_hi is None else agreed_hi
    pts = [p for p in series if p.get("t") is not None]
    if len(pts) < 6:
        return {"kind": "unknown", "why": "not enough readings yet"}

    if peer_disagreement:
        return {
            "kind": "suspect sensor",
            "why": "This node disagrees with the others on the same truck. The "
                   "load is probably fine and the thermometer is probably not.",
            "action": "Check the node before acting on its readings.",
        }

    above = [p for p in pts if p["t"] > hi]
    if not above:
        return {"kind": "normal", "why": "Nothing went above the ideal range.",
                "action": "None."}

    # How the excursion behaved tells you what caused it.
    first_i = pts.index(above[0])
    peak = max(p["t"] for p in above)
    tail = pts[-6:]
    recovered = all(p["t"] <= hi for p in tail)
    rise = above[0]["t"] - pts[max(0, first_i - 1)]["t"]

    if recovered and rise > 1.5 and len(above) <= 12:
        return {
            "kind": "door opening",
            "why": f"A sharp rise to {peak:.1f} °C and a quick return to range. "
                   f"That is the shape of a door opened at a checkpoint, not of "
                   f"cooling failing.",
            "action": "Note it against the checkpoint. No intervention needed.",
        }
    if not recovered:
        return {
            "kind": "cooling failure",
            "why": f"Temperature reached {peak:.1f} °C and has not come back "
                   f"into range. Nothing is bringing this load back down on its own.",
            "action": "Check the reefer now. This is the alert worth a phone call.",
        }
    return {
        "kind": "extended excursion",
        "why": f"The load sat above {hi:.0f} °C for "
               f"{len(above) * 5} minutes before recovering.",
        "action": "Shelf life has been shortened; see the estimate.",
    }
