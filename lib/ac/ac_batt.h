// AnnaChain — battery millivolts to percent.
//
// Pure arithmetic, no Arduino, so tools/selftest.cpp (plain g++) can test it;
// Sht40Sensors::read() in ac_esp.cpp calls it with the ADC reading.
//
// It used to be inline in read(), in unsigned arithmetic: below 3.3 V,
// `mv - 3300` wrapped to about four billion before the clamp could see a
// negative number, so a flat battery read 100 % and the battery alert
// (backend/alerts.py, BATTERY_LOW_PCT) could never fire from a real board.
#pragma once
#include <stdint.h>

namespace ac {

constexpr int kBattEmptyMv = 3300;   // rough Li-ion curve: 3.3 V is empty
constexpr int kBattFullMv  = 4200;   // and 4.2 V is full

// mv: the cell voltage in millivolts (after undoing the 2:1 divider).
// Signed arithmetic, clamped to 0..100.
inline uint8_t batteryPercent(uint32_t mv) {
  if (mv > 100000u) mv = 100000u;                   // no cell is 100 V; keeps the int safe
  int pct = ((int)mv - kBattEmptyMv) * 100 / (kBattFullMv - kBattEmptyMv);
  return (uint8_t)(pct < 0 ? 0 : pct > 100 ? 100 : pct);
}

}  // namespace ac
