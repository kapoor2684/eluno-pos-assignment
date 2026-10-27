"""Core domain types. Nothing here knows about Shopify, HTTP or SQLite."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

@dataclass(frozen=True)
class LensSpec:
    lens_type: str; index: float; coating: str
    sph_min: float; sph_max: float; cyl_abs_max: float
    add_required: bool; add_min: Optional[float]; add_max: Optional[float]

@dataclass(frozen=True)
class Product:
    ref: int; sku: str; title: str; line_type: str   # 'frame' | 'lens'
    price: int; hsn: str; category: Optional[str] = None
    rx_able: bool = False; archived: bool = False
    stock_ref: Optional[int] = None                  # set => inventory tracked
    lens: Optional[LensSpec] = None

@dataclass(frozen=True)
class Eye:
    sph: float; cyl: float; axis: Optional[int]; add: Optional[float]

@dataclass(frozen=True)
class Rx:
    rx_id: str; source: str; date: date; valid_until: date; od: Eye; os: Eye; pd: dict

@dataclass(frozen=True)
class Customer:
    ref: int; name: str; phone: str; tags: frozenset

@dataclass
class CartPair:
    frame: Optional[Product] = None
    lens: Optional[Product] = None
    rx: Optional[Rx] = None

@dataclass
class ManualDiscount:
    bps: int; reason_code: str; approver_id: str

@dataclass
class Cart:
    store_code: str; location_ref: int; staff_id: str; customer: Customer
    pairs: List[CartPair]; channel: str; payment_method: str
    manual: Optional[ManualDiscount] = None

@dataclass
class BillLine:
    pair_ref: int; product: Product; gross: int
    discounts: Dict[str, int] = field(default_factory=dict)   # offer_id -> paise
    rx_id: Optional[str] = None; rx_snapshot: Optional[dict] = None
    rate_bps: int = 0; taxable: int = 0; cgst: int = 0; sgst: int = 0
    @property
    def discount(self): return sum(self.discounts.values())
    @property
    def net(self): return self.gross - self.discount

@dataclass
class Bill:
    lines: List[BillLine]; evaluations: List[dict]
    @property
    def gross(self): return sum(l.gross for l in self.lines)
    @property
    def discount(self): return sum(l.discount for l in self.lines)
    @property
    def total(self): return sum(l.net for l in self.lines)
    @property
    def taxable(self): return sum(l.taxable for l in self.lines)
    @property
    def cgst(self): return sum(l.cgst for l in self.lines)
    @property
    def sgst(self): return sum(l.sgst for l in self.lines)

# ---- what comes back from the system of record, in our terms ----
@dataclass
class PlacedLine:
    ref: int; sku: str; line_type: str; quantity: int; gross: int; discount: int; tax: int
@dataclass
class PlacedRefund:
    ref: int; created_at: datetime; amount: int; credit_note_no: Optional[str]
    lines: List[Tuple[int, int, int]]   # (line_ref, subtotal, tax) reversed
    key: Optional[str] = None
@dataclass
class PlacedOrder:
    ref: int; label: str; created_at: datetime; location_ref: int
    invoice_no: Optional[str]; bill_key: Optional[str]; channel: str; payment_method: str; staff_id: Optional[str]
    lines: List[PlacedLine]; sale_tx_ref: Optional[int]; sale_gateway: Optional[str]; sale_auth: Optional[str]; sale_total: int
    refunds: List[PlacedRefund]; status: str
    @property
    def gross(self): return sum(l.gross for l in self.lines)
    @property
    def discount(self): return sum(l.discount for l in self.lines)
    @property
    def tax(self): return sum(l.tax for l in self.lines)
    @property
    def refunded(self): return sum(r.amount for r in self.refunds)

class BlockedError(Exception):
    """A business rule stops the bill. Message is written for the person at the till."""
    def __init__(self, issues): self.issues = issues; super().__init__("; ".join(i["message"] for i in issues))
