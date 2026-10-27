"""Use-cases: build cart, bill (charge -> number -> Shopify), refund, resume, till, day-end. Journal-first so a crash never loses a paid bill."""
import json, uuid
from dataclasses import asdict
from datetime import date
from typing import Optional
from .domain import Cart, CartPair, ManualDiscount, BlockedError, Customer
from .adapters.http import Ambiguous, HttpError
from .money import inr
from .pricing import check_cart, price_cart
from .report import dayend as _dayend
from .store import Store, now

CHANNELS = ("walk_in", "whatsapp_booked", "online_pickup")
METHODS = ("cash", "upi", "card")
CASH_ALERT = 20000  # Finance: variance over ₹200 flagged the same night

class Pos:
    def __init__(self, commerce, rx_source, terminal, store: Store, offers: list, rates, today=lambda: now().date()):
        self.c, self.smt, self.t, self.s, self.offers, self.rates, self.today = commerce, rx_source, terminal, store, offers, rates, today
        self._locs = None; self._catalog = None

    # ---------- reference data ----------
    def loc(self, store_code):
        self._locs = self._locs or self.c.locations()
        l = next((x for x in self._locs if x.store_code == store_code and x.kind == "store"), None)
        if not l: raise BlockedError([{"level": "block", "code": "STORE", "message": f"Unknown store {store_code}."}])
        return l
    def catalog(self):
        self._catalog = self._catalog or self.c.catalog(); return self._catalog
    def staff(self, store_code, staff_id):
        s = next((x for x in self.smt.staff(store_code) if x["staff_id"] == staff_id), None)
        if not s: raise BlockedError([{"level": "block", "code": "STAFF", "message": f"{staff_id} is not staff at {store_code}."}])
        return s

    # ---------- cart ----------
    def lookup(self, phone):
        cust = self.c.find_customer(phone); rxs = self.smt.prescriptions(phone) if cust else []
        valid = next((r for r in rxs if r.valid_until >= self.today()), None)
        return cust, valid, rxs

    def build_cart(self, store_code, staff_id, phone, pairs, channel="walk_in", method="upi", manual=None) -> Cart:
        """pairs: [(frame_sku|None, lens_sku|None)]"""
        if channel not in CHANNELS: raise BlockedError([{"level": "block", "code": "CHANNEL", "message": f"channel must be one of {CHANNELS}"}])
        if method not in METHODS: raise BlockedError([{"level": "block", "code": "METHOD", "message": f"payment method must be one of {METHODS}"}])
        loc = self.loc(store_code); staff = self.staff(store_code, staff_id)
        cust, rx, rxs = self.lookup(phone)
        if not cust: raise BlockedError([{"level": "block", "code": "NO_CUSTOMER", "message": f"No customer with phone {phone} in Shopify. Create the customer first."}])
        rx = rx or (rxs[0] if rxs else None)         # an expired Rx is still passed on so the block message is specific
        by_sku = {p.sku: p for p in self.catalog()}
        cp = []
        for f, l in pairs:
            for sku, kind in ((f, "frame"), (l, "lens")):
                if sku and (sku not in by_sku or by_sku[sku].line_type != kind): raise BlockedError([{"level": "block", "code": "SKU", "message": f"{sku} is not a known {kind} SKU."}])
            cp.append(CartPair(by_sku[f] if f else None, by_sku[l] if l else None, rx))
        mgr = None
        if manual:
            ap = self.staff(store_code, manual["approver_id"])
            if ap["role"] != "store_manager" or not ap.get("can_override_discount"):
                raise BlockedError([{"level": "block", "code": "APPROVER", "message": f"{ap['staff_id']} ({ap['role']}) cannot approve discounts. A store manager must."}])
            if not manual.get("reason_code"): raise BlockedError([{"level": "block", "code": "REASON", "message": "Manual discount needs a reason code."}])
            mgr = ManualDiscount(manual["bps"], manual["reason_code"], manual["approver_id"])
        return Cart(store_code, loc.ref, staff_id, cust, cp, channel, method, mgr)

    def quote(self, cart: Cart, confirmed_by: Optional[str] = None):
        on = self.today(); frames = [p.frame for p in cart.pairs if p.frame]
        if confirmed_by and self.staff(cart.store_code, confirmed_by)["role"] != "optometrist":
            raise BlockedError([{"level": "block", "code": "CONFIRM", "message": "Only an optometrist can confirm a prescription warning."}])
        issues = check_cart(cart, on, [p for p in self.catalog() if p.lens], self.c.stock(frames, cart.location_ref) if frames else {}, confirmed_by)
        blocks = [i for i in issues if i["level"] in ("block", "warn")]
        if blocks: raise BlockedError(blocks)
        return price_cart(cart, self.offers, self.rates, on), issues

    # ---------- bill ----------
    def _draft(self, cart: Cart, bill, bill_id):
        lines = [{"variant_ref": l.product.ref, "sku": l.product.sku, "line_type": l.product.line_type, "hsn": l.product.hsn, "pair_ref": l.pair_ref, "gross": l.gross,
                  "discounts": l.discounts, "rate_bps": l.rate_bps, "taxable": l.taxable, "cgst": l.cgst, "sgst": l.sgst, "rx_id": l.rx_id, "rx_snapshot": l.rx_snapshot,
                  "lens_config": f"{l.product.lens.lens_type}/{l.product.lens.index}/{l.product.lens.coating}" if l.product.lens else None, "title": l.product.title} for l in bill.lines]
        offers = [{"offer_id": e["offer_id"], "amount": e["amount"]} for e in bill.evaluations if e["applied"]]
        return {"bill_key": bill_id, "store_code": cart.store_code, "location_ref": cart.location_ref, "customer_ref": cart.customer.ref, "customer": f"{cart.customer.name} {cart.customer.phone}",
                "staff_id": cart.staff_id, "channel": cart.channel, "payment_method": cart.payment_method, "lines": lines, "offers": offers, "total": bill.total,
                "manual": {"reason_code": cart.manual.reason_code, "approver_id": cart.manual.approver_id} if cart.manual else None, "date": str(self.today())}

    def checkout(self, cart: Cart, confirmed_by=None, bill_id=None):
        bill, _ = self.quote(cart, confirmed_by)
        if bill.total <= 0: raise BlockedError([{"level": "block", "code": "ZERO", "message": "Zero-value bills (remakes) are not supported in v1."}])
        if cart.payment_method == "cash" and not self.s.open_session(cart.store_code):
            raise BlockedError([{"level": "block", "code": "NO_TILL", "message": "Open the cash till before taking cash."}])
        bill_id = bill_id or str(uuid.uuid4()); draft = self._draft(cart, bill, bill_id)
        self.s.new_bill(bill_id, cart.store_code, bill.total, cart.payment_method, draft)   # journal BEFORE money moves
        self.s.audit(cart.staff_id, "bill.started", bill_id, cart.manual.reason_code if cart.manual else None, total=bill.total, manual=draft["manual"])
        return self.advance(bill_id)

    def advance(self, bill_id):
        """Drive a journalled bill forward from whatever state it is in. Safe to call repeatedly."""
        b = self.s.bill(bill_id); d = json.loads(b["draft"]); loc = self.loc(b["store_code"])
        if b["state"] == "PRICED":
            try:
                ref = ("cash:" + bill_id) if b["method"] == "cash" else self.t.charge(b["total"], b["method"], bill_id)   # terminal idempotent on order_ref
            except Exception as e:
                self.s.update_bill(bill_id, state="FAILED", error=str(e)); self.s.audit(d["staff_id"], "bill.payment_failed", bill_id, detail=str(e)); raise
            self.s.update_bill(bill_id, state="CHARGED", charge_ref=ref); b = self.s.bill(bill_id)
        if b["state"] == "CHARGED":
            no = self.s.allocate(loc.invoice_prefix, "invoice", bill_id, lambda: self._series_floor(loc, loc.invoice_prefix))
            self.s.update_bill(bill_id, state="NUMBERED", invoice_no=no); b = self.s.bill(bill_id)
            if b["method"] == "cash":
                sess = self.s.open_session(b["store_code"]) or self._raise_no_till()
                self.s.add_movement(sess["session_id"], "cash_sale", b["total"], no, None, d["staff_id"])
        if b["state"] == "NUMBERED":
            d.update(invoice_no=b["invoice_no"], charge_ref=b["charge_ref"], maybe_sent=bool(b["maybe_sent"]))
            self.s.update_bill(bill_id, maybe_sent=1)                       # from here on a lost response is ambiguous
            try: order = self.c.create_order(d)
            except HttpError as e:   # Shopify refused the order (4xx): retrying will not help; a human must void/refund the capture
                self.s.update_bill(bill_id, state="REJECTED", error=str(e)); self.s.audit(d["staff_id"], "bill.shopify_rejected", b["invoice_no"], detail=str(e)); raise
            except Ambiguous as e:
                self.s.update_bill(bill_id, error=str(e)); self.s.audit(d["staff_id"], "bill.sync_pending", bill_id, detail=str(e)); raise PendingSync(bill_id, b["invoice_no"], str(e))
            self.s.update_bill(bill_id, state="SYNCED", order_ref=order.ref, error=None)
            self.s.audit(d["staff_id"], "bill.synced", b["invoice_no"], shopify=order.label)
        return self.s.bill(bill_id)
    def _raise_no_till(self): raise BlockedError([{"level": "block", "code": "NO_TILL", "message": "Cash taken but no open till session."}])
    def _series_floor(self, loc, prefix):
        nums = [int(n[len(prefix):]) for n in self.c.existing_invoice_numbers(loc.ref) if n.startswith(prefix) and n[len(prefix):].isdigit()]
        return max(nums, default=0)

    def resume(self):
        done = []
        for b in self.s.pending():
            try: done.append(self.advance(b["bill_id"]))
            except PendingSync: pass
        for r in self.s.pending_refunds():
            try: self._refund_advance(r["refund_id"])
            except PendingSync: pass
        return done

    # ---------- refund (full) ----------
    def refund_full(self, invoice_no, reason_code, staff_id):
        if not reason_code: raise BlockedError([{"level": "block", "code": "REASON", "message": "A reason code is required for a refund."}])
        store_code = invoice_no.split("/")[1]; loc = self.loc(store_code); self.staff(store_code, staff_id)
        jb = self.s.bill_by_invoice(invoice_no)
        order = self.c.get_order(jb["order_ref"]) if jb and jb["order_ref"] else self.c.find_order_by_invoice(invoice_no, loc.ref)
        if not order: raise BlockedError([{"level": "block", "code": "NOT_FOUND", "message": f"Invoice {invoice_no} not found in Shopify."}])
        if order.refunds or order.status == "refunded": raise BlockedError([{"level": "block", "code": "ALREADY", "message": f"{invoice_no} is already refunded."}])
        if order.sale_total <= 0: raise BlockedError([{"level": "block", "code": "ZERO", "message": "Nothing to refund."}])
        if order.payment_method == "cash" and not self.s.open_session(store_code): raise BlockedError([{"level": "block", "code": "NO_TILL", "message": "Open the cash till to pay a cash refund."}])
        rid = str(uuid.uuid4())
        self.s.new_refund(refund_id=rid, bill_id=(jb or {}).get("bill_id"), state="PENDING", amount=order.sale_total, method=order.payment_method,
                          reason=reason_code, staff_id=staff_id, store_code=store_code, order_ref=order.ref, charge_ref=order.sale_auth)
        self.s.audit(staff_id, "refund.started", invoice_no, reason_code)
        return self._refund_advance(rid, order)

    def _refund_advance(self, rid, order=None):
        """PENDING -> MONEY_OUT (cash drawer or terminal) -> NUMBERED (credit note allocated only once money moved: no burnt numbers) -> DONE (Shopify)."""
        r = self.s.refund(rid)
        if r["state"] in ("DONE", "FAILED"): return r
        loc = self.loc(r["store_code"]); order = order or self.c.get_order(r["order_ref"])
        if r["state"] == "PENDING":
            try:
                if r["method"] == "cash":
                    sess = self.s.open_session(r["store_code"]) or self._raise_no_till()
                    self.s.add_movement(sess["session_id"], "refund", -r["amount"], "RF-" + rid[:8], r["reason"], r["staff_id"]); tref = "cash"
                else:
                    if r["terminal_ref"] == "SENDING" and self.t.refunded_paise(r["charge_ref"]) >= r["amount"]: tref = "recovered"   # earlier attempt landed, response was lost
                    else:
                        self.s.update_refund(rid, terminal_ref="SENDING"); tref = self.t.refund(r["charge_ref"], r["amount"], r["reason"])
            except Ambiguous as e: raise PendingSync(rid, "refund", str(e))
            except HttpError as e:
                self.s.update_refund(rid, state="FAILED"); self.s.audit(r["staff_id"], "refund.payment_failed", rid, r["reason"], detail=str(e)); raise
            self.s.update_refund(rid, state="MONEY_OUT", terminal_ref=tref); r = self.s.refund(rid)
        if r["state"] == "MONEY_OUT":
            cn = self.s.allocate(loc.invoice_prefix + "CN/", "credit_note", rid, lambda: 0)
            self.s.update_refund(rid, state="NUMBERED", credit_note_no=cn); r = self.s.refund(rid)
        if r["state"] == "NUMBERED":
            req = {"refund_key": rid, "credit_note_no": r["credit_note_no"], "reason": r["reason"], "staff_id": r["staff_id"], "amount": r["amount"], "location_ref": order.location_ref,
                   "maybe_sent": bool(r["maybe_sent"]), "lines": [{"line_ref": l.ref, "line_type": l.line_type, "qty": l.quantity} for l in order.lines]}
            self.s.update_refund(rid, maybe_sent=1)
            try: pr = self.c.refund_order(order, req)
            except (Ambiguous, HttpError) as e: self.s.audit(r["staff_id"], "refund.sync_pending", rid, detail=str(e)); raise PendingSync(rid, r["credit_note_no"], str(e))
            self.s.update_refund(rid, state="DONE", shopify_ref=pr.ref); self.s.audit(r["staff_id"], "refund.done", r["credit_note_no"], r["reason"], amount=r["amount"])
        return self.s.refund(rid)

    # ---------- till ----------
    def till_open(self, store_code, staff_id, float_paise):
        self.staff(store_code, staff_id)
        if self.s.open_session(store_code): raise BlockedError([{"level": "block", "code": "TILL_OPEN", "message": "A till session is already open."}])
        sid = f"TILL-{store_code}-{self.today()}"; n = len(self.s.sessions_on(store_code, str(self.today())))
        sid = sid if n == 0 else f"{sid}-{n+1}"
        self.s.db.execute("INSERT INTO till_sessions(session_id,store_code,opened_at,opened_by,float) VALUES(?,?,?,?,?)", (sid, store_code, now().isoformat(timespec="seconds"), staff_id, float_paise))
        self.s.audit(staff_id, "till.opened", sid, float=float_paise); return sid
    def till_move(self, store_code, staff_id, kind, amount, reason):
        if kind not in ("paid_in", "paid_out"): raise ValueError(kind)
        if not reason: raise BlockedError([{"level": "block", "code": "REASON", "message": "Paid-in/out needs a reason."}])
        self.staff(store_code, staff_id); sess = self.s.open_session(store_code) or self._raise_no_till()
        ref = f"{'PI' if kind=='paid_in' else 'PO'}-{store_code}-{uuid.uuid4().hex[:6]}"
        self.s.add_movement(sess["session_id"], kind, amount if kind == "paid_in" else -amount, ref, reason, staff_id); self.s.audit(staff_id, f"till.{kind}", ref, reason, amount=amount); return ref
    def till_close(self, store_code, staff_id, counted):
        st = self.staff(store_code, staff_id)
        if st["role"] != "store_manager": raise BlockedError([{"level": "block", "code": "ROLE", "message": "Only the store manager closes the till."}])
        sess = self.s.open_session(store_code) or self._raise_no_till()
        expected = sess["float"] + sum(m["amount"] for m in self.s.movements(sess["session_id"])); var = counted - expected
        self.s.db.execute("UPDATE till_sessions SET closed_at=?,closed_by=?,expected=?,counted=?,variance=? WHERE session_id=?", (now().isoformat(timespec="seconds"), staff_id, expected, counted, var, sess["session_id"]))
        self.s.audit(staff_id, "till.closed", sess["session_id"], expected=expected, counted=counted, variance=var)
        return {"session_id": sess["session_id"], "expected": expected, "counted": counted, "variance": var, "alert_finance": abs(var) > CASH_ALERT}

    # ---------- day-end ----------
    def day_end(self, store_code, day: date):
        loc = self.loc(store_code); orders = self.c.orders(loc.ref, since_days=max(30, (self.today() - day).days + 30))
        sessions = self.s.sessions_on(store_code, str(day)); mv = [m for s in sessions for m in self.s.movements(s["session_id"])]
        rep = _dayend(orders, day, self.s.bills_for(store_code, str(day)), mv)
        rep["store"] = store_code; rep["till_sessions"] = [dict({k: s[k] for k in ("session_id", "float", "expected", "counted", "variance")}, alert_finance=s["variance"] is not None and abs(s["variance"]) > CASH_ALERT) for s in sessions]
        return rep

class PendingSync(Exception):
    def __init__(self, ref, number, why): self.ref, self.number, self.why = ref, number, why; super().__init__(f"{number}: paid and numbered, Shopify sync pending ({why}). Run `resume`.")
