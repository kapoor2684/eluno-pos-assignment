"""Prescription <-> lens validation. Returns issues with a code, level (block|warn) and an actionable message."""
from datetime import date
from typing import List
from .domain import Rx, Product

def _d(x): return f"{x:+.2f}"

def validate_lens(rx: Rx, lens: Product, on: date, matrix: List[Product] = ()) -> List[dict]:
    out, L = [], lens.lens
    def add(level, code, msg): out.append({"level": level, "code": code, "message": msg})
    if rx.valid_until < on:
        add("block", "RX_EXPIRED", f"Prescription {rx.rx_id} expired on {rx.valid_until}. Send the customer for a new eye test.")
    name = f"{L.index} {L.lens_type.replace('_', ' ')}"
    for side, e in (("Right (OD)", rx.od), ("Left (OS)", rx.os)):
        if not L.sph_min <= e.sph <= L.sph_max:
            alts = sorted({m.lens.index for m in matrix if m.lens and m.lens.lens_type == L.lens_type
                           and m.lens.sph_min <= rx.od.sph <= m.lens.sph_max and m.lens.sph_min <= rx.os.sph <= m.lens.sph_max
                           and abs(rx.od.cyl) <= m.lens.cyl_abs_max and abs(rx.os.cyl) <= m.lens.cyl_abs_max})
            hint = f" Indexes that fit both eyes: {', '.join(map(str, alts))}." if alts else f" No {L.lens_type.replace('_',' ')} lens in the matrix covers this Rx; refer to the optometrist."
            add("block", "SPH_OUT_OF_RANGE", f"{side} sphere {_d(e.sph)} is outside the {name} range ({_d(L.sph_min)} to {_d(L.sph_max)}).{hint}")
        if abs(e.cyl) > L.cyl_abs_max:
            add("block", "CYL_OUT_OF_RANGE", f"{side} cylinder {_d(e.cyl)} exceeds the {name} limit of {L.cyl_abs_max:.2f}. Choose a lens that supports it or refer to the optometrist.")
        if e.cyl != 0 and e.axis is None:
            add("block", "AXIS_MISSING", f"{side} has cylinder {_d(e.cyl)} but no axis. Ask the optometrist to complete the prescription.")
        if e.cyl != 0 and e.axis is not None and (e.axis % 5 != 0 or not 0 <= e.axis <= 180):
            add("warn", "AXIS_OFF_GRID", f"{side} axis {e.axis}° is not on the 5° grid (source: {rx.source}). An optometrist must confirm before billing.")
        if L.add_required:
            if e.add is None:
                add("block", "ADD_MISSING", f"{side} has no ADD, which {L.lens_type} lenses need. Ask the optometrist to add it or choose single vision.")
            elif L.add_min is not None and not L.add_min <= e.add <= L.add_max:
                add("block", "ADD_OUT_OF_RANGE", f"{side} ADD {_d(e.add)} is outside the {name} range ({_d(L.add_min)} to {_d(L.add_max)}).")
    return out
