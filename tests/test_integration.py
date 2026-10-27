"""Runs the real mock server in-process (no flake) and drives the full flow through the real adapters."""
import importlib.util, json, os, threading, unittest
from datetime import date
from http.server import HTTPServer
from eluno_pos.adapters.http import Http, Ambiguous
from eluno_pos.adapters.shopify import ShopifyAdapter
from eluno_pos.adapters.smt import SmtAdapter
from eluno_pos.adapters.terminal import TerminalAdapter
from eluno_pos.domain import BlockedError
from eluno_pos.service import Pos, PendingSync
from eluno_pos.store import Store, now
from eluno_pos.tax import RateTable

ROOT = os.path.join(os.path.dirname(__file__), ".."); D = os.path.join(ROOT, "data")
def fresh_mock():
    spec = importlib.util.spec_from_file_location("mock_server", os.path.join(ROOT, "mock-apis", "mock_server.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); m.FLAKE = False; m.H.log_message = lambda *a: None; return m

class Crash(BaseException): pass

class LossyHttp(Http):
    """Performs the call on the server, then pretends the response was lost (the nasty Wi-Fi case)."""
    def __init__(self, *a, lose_posts=0, storm_429=0, crash_posts=0, **k): super().__init__(*a, **k); self.lose, self.storm, self.crash, self.log = lose_posts, storm_429, crash_posts, []
    def _once(self, method, path, body):
        if method == "POST" and path.endswith("/orders.json") and self.storm > 0:
            self.storm -= 1; self.log.append("429"); return 429, {"errors": "slow down"}, {"Retry-After": "0"}
        out = super()._once(method, path, body)
        if method == "POST" and path.endswith("/orders.json") and self.crash > 0:
            self.crash -= 1; raise Crash()                      # the process dies after the server created the order
        if method == "POST" and (path.endswith("/orders.json") or path.endswith("/refunds.json")) and self.lose > 0:
            self.lose -= 1; self.log.append("lost"); raise Ambiguous("response lost")
        return out

class Base(unittest.TestCase):
    PHONE = "+919000000005"
    def setUp(self):   # fresh mock state per test
        mock = fresh_mock(); mock.S["inventory"][(92000003, 70000002)]["available"] = 20; self.srv = HTTPServer(("127.0.0.1", 0), mock.H); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
    def tearDown(self): self.srv.shutdown(); self.srv.server_close()
    def pos(self, **kw):
        http = LossyHttp(self.base, sleep=lambda s: None, **kw)
        self.http = http
        return Pos(ShopifyAdapter(http), SmtAdapter(http), TerminalAdapter(http), Store(), json.load(open(f"{D}/offers.json")), RateTable.load(f"{D}/tax_rates.json"))
    def stock(self, p, sku_item=92000003, loc=70000002): return p.c.http.request("GET", f"/admin/api/2026-10/inventory_levels.json?inventory_item_ids={sku_item}&location_ids={loc}")["inventory_levels"][0]["available"]
    def orders_with_key(self, p, key): return [o for o in p.c.orders(70000002, 5) if o.bill_key == key]
    def cart(self, p, pairs=(("FR-0003", "LN-0043"),), pay="upi"): return p.build_cart("HSR", "S105", self.PHONE, list(pairs), "walk_in", pay)

class FlowTests(Base):
    def test_bill_then_full_refund_restocks_frame_only(self):
        p = self.pos(); before = self.stock(p)
        row = p.checkout(self.cart(p)); self.assertEqual(row["state"], "SYNCED"); self.assertEqual(self.stock(p), before - 1)
        o = p.c.get_order(row["order_ref"]); self.assertEqual(o.invoice_no, row["invoice_no"]); self.assertNotEqual(o.label, o.invoice_no)
        self.assertEqual(o.sale_total, 1869700); self.assertEqual(o.gross - o.discount, o.sale_total); self.assertEqual(len([l for l in o.lines if l.line_type == "lens"]), 1)
        r = p.refund_full(row["invoice_no"], "CHANGED_MIND", "S105"); self.assertEqual(r["state"], "DONE"); self.assertTrue(r["credit_note_no"].startswith("ELU/HSR/FY26/CN/"))
        self.assertEqual(self.stock(p), before)                        # frame back at the billing store
        o2 = p.c.get_order(row["order_ref"]); self.assertEqual(o2.status, "refunded"); self.assertEqual(o2.refunded, o.sale_total)
        self.assertEqual(sum(t for _, _, t in o2.refunds[0].lines), o.tax)  # tax reversed per line, in full
        with self.assertRaises(BlockedError): p.refund_full(row["invoice_no"], "AGAIN", "S105")
    def test_invoice_series_continues_from_shopify_and_is_gapless(self):
        p = self.pos(); nos = [p.checkout(self.cart(p, [("FR-0003", None)]))["invoice_no"] for _ in range(3)]
        n = [int(x.rsplit("/", 1)[1]) for x in nos]; self.assertEqual(n, list(range(n[0], n[0] + 3))); self.assertGreaterEqual(n[0], 3)  # seed has 00001-00002 at HSR... never reuses them
    def test_payment_failure_burns_no_invoice_number(self):
        p = self.pos(); first = int(p.checkout(self.cart(p, [("FR-0003", None)]))["invoice_no"].rsplit("/", 1)[1])
        p.t.charge = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("card declined"))
        with self.assertRaises(RuntimeError): p.checkout(self.cart(p, [("FR-0003", None)]))
        p2 = self.pos(); p2.s = p.s; self.assertEqual(int(p2.checkout(self.cart(p2, [("FR-0003", None)]))["invoice_no"].rsplit("/", 1)[1]), first + 1)
    def test_cash_needs_open_till_and_hits_drawer(self):
        p = self.pos()
        with self.assertRaises(BlockedError): p.checkout(self.cart(p, pay="cash"))
        p.till_open("HSR", "S108", 300000); row = p.checkout(self.cart(p, [("FR-0003", None)], pay="cash"))
        s = p.s.open_session("HSR"); self.assertEqual(sum(m["amount"] for m in p.s.movements(s["session_id"])), row["total"])
        p.till_move("HSR", "S105", "paid_out", 5000, "tea"); res = p.till_close("HSR", "S108", 300000 + row["total"] - 5000 - 25000)
        self.assertEqual(res["variance"], -25000); self.assertTrue(res["alert_finance"])
        rep = p.day_end("HSR", now().date()); self.assertEqual(rep["mismatches"], [])
    def test_dayend_matches_shopify_and_flags_unsynced(self):
        p = self.pos(lose_posts=0); p.checkout(self.cart(p, [("FR-0003", None)])); rep = p.day_end("HSR", now().date())
        self.assertEqual(rep["mismatches"], []); self.assertEqual(rep["net_sales"], rep["by_payment_method"]["upi"]["sales"]); self.assertEqual(rep["taxable"] + rep["tax"], rep["net_sales"])
    def test_blocked_reasons_are_actionable(self):
        p = self.pos()
        with self.assertRaises(BlockedError) as e: p.quote(p.build_cart("HSR", "S105", "+919000000000", [("FR-0003", "LN-0043")]))
        self.assertIn("RX_EXPIRED", [i["code"] for i in e.exception.issues])
        with self.assertRaises(BlockedError) as e: p.build_cart("HSR", "S105", self.PHONE, [("FR-0003", None)], manual={"bps": 500, "reason_code": "X", "approver_id": "S105"})
        self.assertIn("cannot approve", str(e.exception))

class ResilienceTests(Base):
    def test_lost_response_does_not_create_two_orders(self):
        p = self.pos(lose_posts=1); row = p.checkout(self.cart(p, [("FR-0003", None)]))
        self.assertIn("lost", self.http.log); self.assertEqual(row["state"], "SYNCED"); self.assertEqual(len(self.orders_with_key(p, row["bill_id"])), 1)
    def test_429_storm_is_retried_without_duplicates(self):
        p = self.pos(storm_429=4); row = p.checkout(self.cart(p, [("FR-0003", None)])); self.assertEqual(self.http.log.count("429"), 4); self.assertEqual(len(self.orders_with_key(p, row["bill_id"])), 1)
    def test_wifi_down_keeps_paid_bill_and_resume_syncs_once(self):
        p = self.pos(); real = p.c.create_order
        p.c.create_order = lambda d: (_ for _ in ()).throw(Ambiguous("wifi down"))
        with self.assertRaises(PendingSync): p.checkout(self.cart(p, [("FR-0003", None)]))
        b = p.s.pending()[0]; self.assertEqual(b["state"], "NUMBERED"); self.assertTrue(b["invoice_no"])
        rep = p.day_end("HSR", now().date()); self.assertTrue(any("NOT in Shopify yet" in m for m in rep["mismatches"]))
        p.c.create_order = real; p.resume(); p.resume()                         # resume twice: still one order
        self.assertEqual(p.s.bill(b["bill_id"])["state"], "SYNCED"); self.assertEqual(len(self.orders_with_key(p, b["bill_id"])), 1)
    def test_crash_after_lost_response_then_resume_finds_existing_order(self):
        p = self.pos(crash_posts=1)
        with self.assertRaises(Crash): p.checkout(self.cart(p, [("FR-0003", None)]))
        b = p.s.pending()[0]; p2 = self.pos(); p2.s = p.s; p2.resume()
        self.assertEqual(p2.s.bill(b["bill_id"])["state"], "SYNCED"); self.assertEqual(len(self.orders_with_key(p2, b["bill_id"])), 1)
    def test_lost_refund_response_does_not_double_refund(self):
        p = self.pos(); row = p.checkout(self.cart(p, [("FR-0003", None)])); p.c.http.lose = 1
        r = p.refund_full(row["invoice_no"], "CHANGED_MIND", "S105"); self.assertEqual(r["state"], "DONE"); self.assertEqual(len(p.c.get_order(row["order_ref"]).refunds), 1)

if __name__ == "__main__": unittest.main()
