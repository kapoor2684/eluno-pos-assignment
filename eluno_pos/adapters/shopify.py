"""The ONLY module that knows Shopify's shapes. Maps domain drafts -> Shopify REST payloads and orders -> PlacedOrder."""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from ..domain import Customer, Product, LensSpec, PlacedOrder, PlacedLine, PlacedRefund
from ..money import to_paise, to_str
from ..ports import Location
from .http import Http, Ambiguous

API = "/admin/api/2026-10"

def _attr(o, name):
    return next((a["value"] for a in o.get("note_attributes", []) if a["name"] == name), None)

class ShopifyAdapter:
    def __init__(self, http: Http, sleep=None): self.http = http; self.sleep = sleep or http.sleep

    # ---- reads ----
    def locations(self):
        return [Location(l["id"], l["eluno"]["store_code"], l["eluno"]["kind"], l["eluno"]["invoice_prefix"]) for l in self.http.request("GET", f"{API}/locations.json")["locations"]]
    def find_customer(self, phone):
        r = self.http.request("GET", f"{API}/customers/search.json?query={quote('phone:' + phone)}")["customers"]
        if not r: return None
        c = r[0]; return Customer(c["id"], f"{c['first_name']} {c['last_name']}".strip(), c["phone"], frozenset(t.strip() for t in c["tags"].split(",") if t.strip()))
    def catalog(self):
        out = []
        for p in self.http.request("GET", f"{API}/products.json?limit=250")["products"]:
            for v in p["variants"]:
                e = v["eluno"]; lens = None
                if e["line_type"] == "lens":
                    L = e["rx_limits"]; lens = LensSpec(e["lens_type"], e["index"], e["coating"], L["sph_min"], L["sph_max"], L["cyl_abs_max"], L["add_required"], L.get("add_min"), L.get("add_max"))
                out.append(Product(v["id"], v["sku"], p["title"] + f" {v['title']}", e["line_type"], to_paise(v["price"]), e["hsn"],
                                   e.get("category"), e.get("rx_able", False), p["status"] != "active", v["inventory_item_id"], lens))
        return out
    def stock(self, products, location_ref):
        items = [p.stock_ref for p in products if p.stock_ref]
        if not items: return {}
        rows = self.http.request("GET", f"{API}/inventory_levels.json?inventory_item_ids={','.join(map(str, items))}&location_ids={location_ref}")["inventory_levels"]
        by = {r["inventory_item_id"]: r["available"] for r in rows}
        return {p.ref: by.get(p.stock_ref, 0) for p in products if p.stock_ref}

    def _parse(self, o) -> PlacedOrder:
        lines = []
        for li in o["line_items"]:
            props = {p["name"]: p["value"] for p in li["properties"]}
            lines.append(PlacedLine(li["id"], li["sku"], props.get("line_type", "?"), li["quantity"], to_paise(li["price"]) * li["quantity"],
                                    to_paise(li["total_discount"]), sum(to_paise(t["price"]) for t in li["tax_lines"])))
        sales = [t for t in o["transactions"] if t["kind"] == "sale" and t["status"] == "success"]
        refunds = []
        for r in o["refunds"]:
            refunds.append(PlacedRefund(r["id"], datetime.fromisoformat(r["created_at"]), sum(to_paise(t["amount"]) for t in r["transactions"]), _attr(r, "credit_note_no"),
                                        [(x["line_item_id"], to_paise(x["subtotal"]), to_paise(x["total_tax"])) for x in r["refund_line_items"]], _attr(r, "pos_refund_id")))
        return PlacedOrder(o["id"], o["name"], datetime.fromisoformat(o["created_at"]), o["location_id"], _attr(o, "invoice_no"), _attr(o, "pos_bill_id"),
                           _attr(o, "channel") or "unknown", _attr(o, "payment_method") or "unknown", _attr(o, "staff_id"), lines,
                           sales[0]["id"] if sales else None, sales[0].get("gateway") if sales else None, sales[0].get("authorization") if sales else None, sum(to_paise(t["amount"]) for t in sales), refunds, o["financial_status"])
    def get_order(self, ref): return self._parse(self.http.request("GET", f"{API}/orders/{ref}.json")["order"])
    def orders(self, location_ref, since_days=30):
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        # NOTE: real Shopify paginates via Link headers; the mock returns up to `limit`. Filtering by time is also done client-side by callers.
        r = self.http.request("GET", f"{API}/orders.json?status=any&limit=250&location_id={location_ref}&created_at_min={quote(since)}")["orders"]
        return [self._parse(o) for o in r]
    def _all(self, location_ref):  # unfiltered by date (for invoice bootstrap / lookup)
        return [self._parse(o) for o in self.http.request("GET", f"{API}/orders.json?status=any&limit=250&location_id={location_ref}")["orders"]]
    def existing_invoice_numbers(self, location_ref): return [o.invoice_no for o in self._all(location_ref) if o.invoice_no]
    def find_order_by_invoice(self, invoice_no, location_ref):
        return next((o for o in self._all(location_ref) if o.invoice_no == invoice_no), None)
    def _find_by_key(self, key, location_ref):
        return next((o for o in self.orders(location_ref, since_days=2) if o.bill_key == key), None)

    # ---- writes ----
    def create_order(self, d):
        """Shopify order create is NOT idempotent. Key = bill_key, stored in note_attributes. After any ambiguous failure
        (timeout/5xx) we look the order up by key before posting again, so a lost response cannot create a second order."""
        payload = self._order_payload(d); ambiguous = bool(d.get("maybe_sent"))
        for attempt in range(6):
            if ambiguous:
                found = self._find_by_key(d["bill_key"], d["location_ref"])
                if found: return found
            try:
                r = self.http.request("POST", f"{API}/orders.json", payload, idempotent=False)
                return self._parse(r["order"])
            except Ambiguous:
                ambiguous = True; self.sleep(min(0.5 * 2 ** attempt, 5))
        raise Ambiguous("order create not confirmed after retries; bill stays pending and will be resumed")

    def _order_payload(self, d):
        offers = [o for o in d["offers"] if o["amount"] > 0]; idx = {o["offer_id"]: i for i, o in enumerate(offers)}
        lis = []
        for l in d["lines"]:
            allocs = [{"amount": to_str(a), "discount_application_index": idx[oid]} for oid, a in l["discounts"].items() if a > 0]
            rate = round(l["rate_bps"] / 20000, 5)
            props = [{"name": "line_type", "value": l["line_type"]}, {"name": "hsn", "value": l["hsn"]}, {"name": "pair_ref", "value": str(l["pair_ref"])}]
            if l.get("rx_id"):
                props += [{"name": "rx_id", "value": l["rx_id"]}, {"name": "rx", "value": json.dumps(l["rx_snapshot"], separators=(",", ":"))},
                          {"name": "lens_config", "value": l["lens_config"]}]
            lis.append({"variant_id": l["variant_ref"], "quantity": 1, "price": to_str(l["gross"]), "total_discount": to_str(sum(l["discounts"].values())),
                        "tax_lines": [{"title": "CGST", "rate": rate, "price": to_str(l["cgst"])}, {"title": "SGST", "rate": rate, "price": to_str(l["sgst"])}],
                        "discount_allocations": allocs, "properties": props})
        attrs = [("invoice_no", d["invoice_no"]), ("store_code", d["store_code"]), ("channel", d["channel"]), ("staff_id", d["staff_id"]),
                 ("payment_method", d["payment_method"]), ("pos_bill_id", d["bill_key"])]
        if d.get("manual"): attrs += [("discount_reason_code", d["manual"]["reason_code"]), ("discount_approver", d["manual"]["approver_id"])]
        gateway = "cash" if d["payment_method"] == "cash" else "razorpay_terminal"
        return {"order": {"taxes_included": True, "location_id": d["location_ref"], "inventory_behaviour": "decrement_obeying_policy", "source_name": "eluno-pos",
                "customer": {"id": d["customer_ref"]}, "tags": f"pos,{d['channel']}", "note_attributes": [{"name": k, "value": v} for k, v in attrs],
                "line_items": lis,
                "discount_applications": [{"type": "manual", "title": o["offer_id"], "value": to_str(o["amount"]), "value_type": "fixed_amount",
                                           "allocation_method": "each", "target_selection": "explicit", "target_type": "line_item"} for o in offers],
                "transactions": [{"kind": "sale", "status": "success", "gateway": gateway, "amount": to_str(d["total"]), "authorization": d["charge_ref"]}]}}

    def refund_order(self, order, req):
        """req: refund_key, credit_note_no, reason, lines[{line_ref, line_type, qty}], amount, location_ref. Idempotent on refund_key."""
        def existing():
            return next((r for r in self.get_order(order.ref).refunds if r.key == req["refund_key"]), None)
        if req.get("maybe_sent") and (e := existing()): return e
        rl = [{"line_item_id": x["line_ref"], "quantity": x["qty"], "restock_type": "return" if x["line_type"] == "frame" else "no_restock",
               **({"location_id": req["location_ref"]} if x["line_type"] == "frame" else {})} for x in req["lines"]]
        calc = self.http.request("POST", f"{API}/orders/{order.ref}/refunds/calculate.json", {"refund": {"refund_line_items": rl}}, idempotent=True)["refund"]
        if to_paise(calc["transactions"][0]["amount"]) != req["amount"]:
            raise RuntimeError(f"refund mismatch: Shopify calculates {calc['transactions'][0]['amount']}, till computed {to_str(req['amount'])}")
        body = {"refund": {"note": req["reason"], "note_attributes": [{"name": "credit_note_no", "value": req["credit_note_no"]}, {"name": "reason_code", "value": req["reason"]},
                {"name": "pos_refund_id", "value": req["refund_key"]}, {"name": "staff_id", "value": req["staff_id"]}], "refund_line_items": rl,
                "transactions": [{"kind": "refund", "gateway": order.sale_gateway, "amount": to_str(req["amount"]), "parent_id": order.sale_tx_ref}]}}
        for attempt in range(6):
            try:
                r = self.http.request("POST", f"{API}/orders/{order.ref}/refunds.json", body, idempotent=False)["refund"]
                return next(x for x in self.get_order(order.ref).refunds if x.ref == r["id"])
            except Ambiguous:
                self.sleep(min(0.5 * 2 ** attempt, 5))
                if e := existing(): return e
        raise Ambiguous("refund not confirmed; will be resumed")
