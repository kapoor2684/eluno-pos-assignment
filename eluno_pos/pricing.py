"""Cart -> priced Bill: offers, manual discount, per-line GST reconciled to the paisa."""
from datetime import date
from typing import List
from .domain import Cart, Bill, BillLine, BlockedError
from .offers import evaluate
from .money import div_half_up
from .rx import validate_lens
from .tax import RateTable, backout

def _rx_snapshot(rx):
    e = lambda x: {"sph": x.sph, "cyl": x.cyl, "axis": x.axis, "add": x.add}
    return {"od": e(rx.od), "os": e(rx.os), "pd": rx.pd.get("binocular")}

def check_cart(cart: Cart, on: date, matrix, stock, confirmed_by=None) -> List[dict]:
    """All rules that can stop a bill. `stock` is {product.ref: available or None}."""
    issues = []
    if not cart.pairs: issues.append({"level": "block", "code": "EMPTY", "message": "Cart is empty."})
    for n, p in enumerate(cart.pairs, 1):
        if p.frame is None and p.lens is None: issues.append({"level": "block", "code": "EMPTY_PAIR", "message": f"Pair {n} has neither frame nor lens."})
        if p.frame:
            if p.frame.archived: issues.append({"level": "block", "code": "FRAME_ARCHIVED", "message": f"{p.frame.sku} is archived and cannot be sold."})
            avail = stock.get(p.frame.ref)
            if avail is not None and avail < 1: issues.append({"level": "block", "code": "NO_STOCK", "message": f"{p.frame.sku} has no stock at this store. Check nearby stores or order from HQ."})
        if p.lens:
            if p.rx is None: issues.append({"level": "block", "code": "NO_RX", "message": f"Pair {n}: lens needs a prescription, none on file. Send the customer for an eye test."}); continue
            if p.frame and not p.frame.rx_able: issues.append({"level": "block", "code": "FRAME_NOT_RX", "message": f"{p.frame.sku} cannot take prescription lenses."})
            for i in validate_lens(p.rx, p.lens, on, matrix):
                if i["level"] == "warn" and confirmed_by: i = dict(i, level="info", message=i["message"] + f" (confirmed by {confirmed_by})")
                issues.append(dict(i, pair=n))
    return issues

def price_cart(cart: Cart, offers: list, rates: RateTable, on: date) -> Bill:
    lines: List[BillLine] = []
    for n, p in enumerate(cart.pairs, 1):
        for prod in (p.frame, p.lens):
            if prod is None: continue
            bl = BillLine(pair_ref=n, product=prod, gross=prod.price)
            if prod.line_type == "lens": bl.rx_id, bl.rx_snapshot = p.rx.rx_id, _rx_snapshot(p.rx)
            lines.append(bl)
    ctx = [{"i": i, "pair": l.pair_ref, "type": l.product.line_type, "gross": l.gross, "lens": l.product.lens} for i, l in enumerate(lines)]
    if cart.manual:
        # MANAGER_OVERRIDE is non-stackable by data: it replaces automatic offers. We show what they would have given.
        mo = next((o for o in offers if o["offer_id"] == "MANAGER_OVERRIDE"), None)
        cap = mo["rule"]["discount"]["value_bps_max"] if mo else 0
        if cart.manual.bps > cap: raise BlockedError([{"level": "block", "code": "OVER_CAP", "message": f"Manual discount {cart.manual.bps/100:.1f}% exceeds the {cap/100:.1f}% cap."}])
        auto, evals = evaluate(offers, ctx, cart.customer.tags, on)
        would = sum(sum(p.values()) for p in auto.values())
        alloc = {"MANAGER_OVERRIDE": {l["i"]: div_half_up(l["gross"] * cart.manual.bps, 10000) for l in ctx}}
        evals = [dict(e, applied=False, amount=0, why="replaced by manager discount") if e["applied"] else e for e in evals]
        evals.append({"offer_id": "MANAGER_OVERRIDE", "applied": True, "amount": sum(alloc["MANAGER_OVERRIDE"].values()),
                      "why": f"{cart.manual.bps/100:.1f}% reason={cart.manual.reason_code} approver={cart.manual.approver_id}; automatic offers would have given ₹{would/100:,.2f}"})
    else:
        alloc, evals = evaluate(offers, ctx, cart.customer.tags, on)
    for oid, part in alloc.items():
        for i, amt in part.items(): lines[i].discounts[oid] = amt
    for l in lines:
        assert 0 <= l.discount <= l.gross
        l.rate_bps = rates.rate_bps(l.product.hsn, on)
        t = backout(l.net, l.rate_bps)
        l.taxable, l.cgst, l.sgst = t.taxable, t.cgst, t.sgst
        assert l.taxable + l.cgst + l.sgst == l.net
    return Bill(lines, evals)
