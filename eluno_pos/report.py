"""Day-end from Shopify orders (system of record). Pure function over PlacedOrders; cross-checks against the till's own journal and cash drawer."""
from collections import defaultdict
from datetime import date
from typing import List
from .store import IST

def _day(dt): return dt.astimezone(IST).date()

def dayend(orders, day: date, journal: List[dict], cash_movements: List[dict]):
    bills = [o for o in orders if o.invoice_no and _day(o.created_at) == day]
    by_method, by_channel = defaultdict(lambda: {"sales": 0, "refunds": 0}), defaultdict(lambda: {"bills": 0, "net": 0})
    line_tax = {l.ref: l for o in orders for l in o.lines}
    refund_rows = []
    for o in orders:
        for r in o.refunds:
            if _day(r.created_at) == day:
                refund_rows.append((o, r)); by_method[o.payment_method]["refunds"] += r.amount
    for o in bills:
        by_method[o.payment_method]["sales"] += o.sale_total
        by_channel[o.channel]["bills"] += 1; by_channel[o.channel]["net"] += o.gross - o.discount
    gross = sum(o.gross for o in bills); disc = sum(o.discount for o in bills); tax = sum(o.tax for o in bills)
    ref_tax = sum(t for _, r in refund_rows for (_, _, t) in r.lines)
    rep = {"date": str(day), "bill_count": len(bills), "gross": gross, "discounts": disc, "net_sales": gross - disc, "taxable": gross - disc - tax, "tax": tax,
           "refund_count": len(refund_rows), "refunds": sum(r.amount for _, r in refund_rows), "refund_tax_reversed": ref_tax,
           "by_payment_method": {m: dict(v, net=v["sales"] - v["refunds"]) for m, v in sorted(by_method.items())},
           "by_channel": {c: dict(v) for c, v in sorted(by_channel.items())}}
    # --- mismatch checks: never silently trust either side ---
    flags, shop = [], {o.invoice_no: o for o in orders if o.invoice_no}
    for o in bills:
        if o.sale_total != o.gross - o.discount: flags.append(f"{o.invoice_no}: sale transactions {o.sale_total} != order total {o.gross - o.discount}")
    for b in journal:
        o = shop.get(b["invoice_no"]) if b["invoice_no"] else None
        if b["state"] != "SYNCED": flags.append(f"{b['invoice_no'] or b['bill_id']}: paid but NOT in Shopify yet (state {b['state']}) — run `pos resume`")
        elif o is None: flags.append(f"{b['invoice_no']}: in till journal but missing from Shopify")
        elif o.sale_total != b["total"]: flags.append(f"{b['invoice_no']}: till says {b['total']}, Shopify says {o.sale_total}")
    cash_shop = by_method["cash"]["sales"] - by_method["cash"]["refunds"]
    cash_till = sum(m["amount"] for m in cash_movements if m["type"] in ("cash_sale", "refund"))
    if cash_movements or cash_shop:
        if not cash_movements: flags.append(f"cash: Shopify shows net cash {cash_shop} paise but the till has no session/movements for this date")
        elif cash_shop != cash_till: flags.append(f"cash: Shopify net cash {cash_shop} != till drawer movements {cash_till}")
    rep["cash_net_shopify"], rep["cash_net_till"], rep["mismatches"] = cash_shop, cash_till, flags
    return rep
