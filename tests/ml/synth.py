"""Small synthetic hydrograph generator FOR TESTS ONLY. Results computed on this data are never findings."""
from __future__ import annotations

import random

CATS = {"action": 6.0, "minor": 8.0, "moderate": 11.0, "major": 14.0}
T0 = 1_700_006_400.0          # aligned to a whole hour (divisible by 3600)


def hydrograph(site: str = "SYN1", days: int = 120, seed: int = 0, step_s: int = 900, rise_h: float = 8.0,
               n_storms: int = 6, peak: float = 12.0, base: float = 2.0) -> list[dict]:
    """Baseflow + noise + `n_storms` triangular-ish floods (rise over `rise_h` h, recession 3x slower)."""
    rnd = random.Random(seed)
    n = days * 86400 // step_s
    spacing = n // (n_storms + 1)
    starts = [spacing * (k + 1) + rnd.randint(-spacing // 6, spacing // 6) for k in range(n_storms)]
    rise_n, fall_n = int(rise_h * 3600 / step_s), int(rise_h * 3 * 3600 / step_s)
    out = []
    for i in range(n):
        # daily sawtooth of falling baseflow, so a trough exists before each rise (as in real recessions)
        v = base + 2.0 * (1 - (i % 96) / 96) + rnd.uniform(-0.01, 0.01)
        for s in starts:
            if s <= i < s + rise_n:
                v += (peak - base) * ((i - s) / rise_n) ** 1.5
            elif s + rise_n <= i < s + rise_n + fall_n:
                v += (peak - base) * (1 - (i - s - rise_n) / fall_n)
        out.append({"site_no": site, "series": "1", "reading_ts": T0 + i * step_s, "value": round(v, 3),
                    "qualifiers": ["A"], "parameter": "00065"})
    return out
