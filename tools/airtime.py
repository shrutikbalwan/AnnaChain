#!/usr/bin/env python3
"""LoRa time on air for AnnaChain's frames: CALCULATED, not measured.

    python tools/airtime.py

The Semtech SX126x time-on-air formula (SX1261/2 datasheet, section 6.1.4) at
the settings in lib/ac/ac_lora.cpp: SF9, 125 kHz, CR 4/7, 8-symbol preamble,
explicit header, CRC on, low-data-rate optimisation off (a 4.1 ms symbol is
under the 16 ms where it is needed). Every payload is 2 bytes (magic, kind)
plus the frame. docs/HIL.md step 5 says how to measure the real figures; until
that is done, every number this prints is arithmetic.
"""
import math

SF, BW, CR, PREAMBLE = 9, 125_000, 3, 8          # CR 3 = coding rate 4/7
HEADER, CRC, LDRO = 1, 1, 0                      # explicit header, CRC on, LDRO off


def airtime_s(payload_bytes: int) -> float:
    t_sym = (2 ** SF) / BW
    n = 8 * payload_bytes - 4 * SF + 28 + 16 * CRC - 20 * (1 - HEADER)
    n_payload = 8 + max(math.ceil(n / (4 * (SF - 2 * LDRO))) * (CR + 4), 0)
    return (PREAMBLE + 4.25) * t_sym + n_payload * t_sym


FRAMES = {                                       # payload = 2 + frame bytes
    "record (node -> gateway)":        2 + 84,
    "gap notice (node -> gateway)":    2 + 84,
    "query (node -> gateway)":         2 + 4,
    "last-ACK + time (gateway -> node)": 2 + 13,
    "ACK (gateway -> node)":           2 + 8,
}


def per_sample():
    """What one 5-minute sample puts on the air, since S9: query, reply,
    record, ACK. No retries (each can be tried 3 times: LoraNodeLink)."""
    node = airtime_s(FRAMES["query (node -> gateway)"]) + airtime_s(FRAMES["record (node -> gateway)"])
    gw = airtime_s(FRAMES["last-ACK + time (gateway -> node)"]) + airtime_s(FRAMES["ACK (gateway -> node)"])
    return node, gw


if __name__ == "__main__":
    print("CALCULATED (Semtech formula, SF9/125 kHz/CR 4/7), not measured:")
    for name, n in FRAMES.items():
        print(f"  {name:36s} {n:3d} B  {airtime_s(n) * 1000:6.0f} ms")
    node, gw = per_sample()
    print(f"  per 5-minute sample: node transmits {node:.2f} s, gateway {gw:.2f} s per node")
    print(f"  per hour (12 samples): node {12 * node:.1f} s = {12 * node / 36:.2f} % duty; "
          f"gateway with 3 nodes {36 * gw:.1f} s = {36 * gw / 36:.2f} %")
