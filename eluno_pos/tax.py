"""GST. Rates are data (tax_rates.json) with effective dates; prices are GST-inclusive; intra-state => CGST+SGST."""
import json
from dataclasses import dataclass
from datetime import date
from .money import div_half_up

@dataclass(frozen=True)
class TaxSplit:
    rate_bps: int; taxable: int; cgst: int; sgst: int
    @property
    def tax(self): return self.cgst + self.sgst

def backout(gross: int, rate_bps: int) -> TaxSplit:
    """taxable = gross*10000/(10000+bps) half-up; tax = gross - taxable; CGST = half of tax rounded half-up, SGST = remainder."""
    if gross < 0: raise ValueError("negative gross")
    taxable = div_half_up(gross * 10000, 10000 + rate_bps)
    tax = gross - taxable
    cgst = div_half_up(tax, 2)
    return TaxSplit(rate_bps, taxable, cgst, tax - cgst)

class RateTable:
    def __init__(self, rows): self.rows = rows
    @classmethod
    def load(cls, path):
        return cls(json.load(open(path))["rates"])
    def rate_bps(self, hsn: str, on: date) -> int:
        hits = [r for r in self.rows if r["hsn"] == hsn
                and date.fromisoformat(r["effective_from"]) <= on
                and (not r.get("effective_to") or on <= date.fromisoformat(r["effective_to"]))]
        if len(hits) != 1:
            raise LookupError(f"{len(hits)} GST rates match HSN {hsn} on {on}; fix tax_rates.json")
        return hits[0]["rate_bps"]
