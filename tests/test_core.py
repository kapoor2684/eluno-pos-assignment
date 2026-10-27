import json, os, random, unittest
from datetime import date
from eluno_pos.money import allocate, to_paise, to_str
from eluno_pos.tax import backout, RateTable
from eluno_pos.offers import evaluate
from eluno_pos.domain import LensSpec, Rx, Eye, Product, Cart, CartPair, Customer, ManualDiscount, BlockedError
from eluno_pos.pricing import price_cart, check_cart
from eluno_pos.rx import validate_lens

D = os.path.join(os.path.dirname(__file__), "..", "data")
OFFERS = json.load(open(f"{D}/offers.json")); RATES = RateTable.load(f"{D}/tax_rates.json")
DAY = date(2026, 10, 26)

class TaxTests(unittest.TestCase):
    def test_matches_every_seed_order_line_to_the_paisa(self):
        n = 0
        for o in json.load(open(f"{D}/orders_sample.json")):
            for li in o["line_items"]:
                gross = to_paise(li["price"]) * li["quantity"] - to_paise(li["total_discount"])
                bps = int(round(li["tax_lines"][0]["rate"] * 20000)); t = backout(gross, bps)
                self.assertEqual((t.cgst, t.sgst), (to_paise(li["tax_lines"][0]["price"]), to_paise(li["tax_lines"][1]["price"])), o["name"])
                self.assertEqual(t.taxable + t.cgst + t.sgst, gross); n += 1
        self.assertGreaterEqual(n, 12)
    def test_known_line(self):   # sample invoice line 2
        t = backout(807415, 500); self.assertEqual((t.taxable, t.cgst, t.sgst), (768967, 19224, 19224))
    def test_odd_tax_paisa_goes_to_sgst_remainder(self):
        for g in range(1, 3000):
            t = backout(g, 1800); self.assertEqual(t.taxable + t.cgst + t.sgst, g); self.assertIn(t.cgst - t.sgst, (0, 1))
    def test_rate_is_chosen_by_date_not_code(self):
        self.assertEqual(RATES.rate_bps("90015000", date(2025, 9, 21)), 1200)
        self.assertEqual(RATES.rate_bps("90015000", date(2025, 9, 22)), 500)
        with self.assertRaises(LookupError): RATES.rate_bps("00000000", DAY)
    def test_allocate_sums_exactly(self):
        r = random.Random(1)
        for _ in range(500):
            w = [r.randint(1, 999999) for _ in range(r.randint(1, 5))]; tot = r.randint(0, sum(w))
            a = allocate(tot, w); self.assertEqual(sum(a), tot); self.assertTrue(all(0 <= x <= y for x, y in zip(a, w)))

def lens(t="progressive", idx=1.6, coat="blue_cut", smin=-8, smax=6, cyl=4, add=True):
    return LensSpec(t, idx, coat, smin, smax, cyl, add, 0.75, 3.0)
def L(i, pair, typ, gross, ls=None): return {"i": i, "pair": pair, "type": typ, "gross": gross, "lens": ls}

class OfferTests(unittest.TestCase):
    def ev(self, lines, tags=(), on=DAY): return evaluate(OFFERS, lines, frozenset(tags), on)
    def test_picks_best_outcome_not_first_valid(self):   # Karthik: PGX15% alone = 1424.85 but stackables give 1800.00
        lines = [L(0, 1, "frame", 1099800), L(1, 1, "lens", 949900, lens())]
        alloc, ev = self.ev(lines, ["priority", "campaign:progressive_oct26"])
        self.assertEqual(set(alloc), {"PRIORITY500", "FREE_BLUECUT_PGX", "BUNDLE300"}); self.assertEqual(sum(sum(p.values()) for p in alloc.values()), 180000)
        self.assertIn("would give ₹1,424.85", next(e for e in ev if e["offer_id"] == "PGX_CAMPAIGN15")["why"])
    def test_nonstackable_wins_when_better(self):
        alloc, _ = self.ev([L(0, 1, "frame", 1999900)])         # festive 10% capped at ₹2,000 beats bundle (not eligible: no lens)
        self.assertEqual(alloc, {"FESTIVE10": {0: 199990}})
    def test_festive_cap(self):
        alloc, _ = self.ev([L(0, 1, "frame", 5000000)]); self.assertEqual(sum(alloc["FESTIVE10"].values()), 200000)
    def test_second_pair_discount_on_lowest_lens_only(self):
        lines = [L(0, 1, "frame", 500000), L(1, 1, "lens", 100000, lens("single_vision", 1.5, "standard", add=False)), L(2, 2, "frame", 500000), L(3, 2, "lens", 300000, lens("single_vision", 1.5, "standard", add=False))]
        alloc, _ = self.ev(lines, on=date(2026, 11, 20))     # festive expired by then
        self.assertEqual(alloc["SECONDPAIR50"], {1: 50000})
    def test_dates_and_inactive(self):
        _, ev = self.ev([L(0, 1, "frame", 100000)], on=date(2026, 11, 16))
        self.assertIn("outside", next(e for e in ev if e["offer_id"] == "FESTIVE10")["why"]); self.assertEqual(next(e for e in ev if e["offer_id"] == "MONSOON20")["why"], "inactive")
        _, ev = self.ev([L(0, 1, "frame", 100000)], on=date(2026, 11, 15)); self.assertNotIn("outside", next(e for e in ev if e["offer_id"] == "FESTIVE10")["why"])  # valid_to inclusive
    def test_campaign_needs_tag(self):
        _, ev = self.ev([L(0, 1, "lens", 949900, lens())]); self.assertIn("lacks tag", next(e for e in ev if e["offer_id"] == "PGX_CAMPAIGN15")["why"])
    def test_unknown_condition_fails_closed(self):
        o = [{"offer_id": "X", "active": True, "valid_from": "2026-01-01", "valid_to": "2026-12-31", "stackable": True, "priority": 1,
              "rule": {"applies_to": "cart", "condition": {"birthday_month": True}, "discount": {"type": "flat", "value_paise": 100}}}]
        alloc, ev = evaluate(o, [L(0, 1, "frame", 10000)], frozenset(), DAY); self.assertEqual(alloc, {}); self.assertIn("unsupported", ev[0]["why"])
    def test_discount_never_exceeds_line(self):
        o = [dict(OFFERS[2], rule={"applies_to": "cart", "discount": {"type": "flat", "value_paise": 999999}})]
        alloc, _ = evaluate(o, [L(0, 1, "frame", 30000), L(1, 1, "lens", 10000, lens())], frozenset(), DAY)
        self.assertEqual(alloc["PRIORITY500"], {0: 30000, 1: 10000})

def prod(ref, typ, price, hsn, ls=None, **kw): return Product(ref, f"S{ref}", f"P{ref}", typ, price, hsn, lens=ls, rx_able=True, **kw)
RX = Rx("RX1", "in_store", date(2026, 7, 1), date(2027, 7, 1), Eye(-3.0, -0.5, 90, 2.0), Eye(-3.25, 0, None, 2.0), {"binocular": 62})
CUST = Customer(1, "A B", "+91900", frozenset({"priority"}))

class PricingTests(unittest.TestCase):
    def test_bill_reconciles_for_random_carts(self):
        r = random.Random(3)
        for _ in range(300):
            pairs = [CartPair(prod(1, "frame", r.randint(100, 2000000), "90031900"), prod(2, "lens", r.randint(100, 2000000), "90015000", lens()), RX) for _ in range(r.randint(1, 3))]
            bill = price_cart(Cart("HSR", 1, "S1", CUST, pairs, "walk_in", "upi"), OFFERS, RATES, DAY)
            for l in bill.lines: self.assertEqual(l.taxable + l.cgst + l.sgst, l.net); self.assertGreaterEqual(l.net, 0)
            self.assertEqual(bill.total, bill.gross - bill.discount); self.assertEqual(bill.total, bill.taxable + bill.cgst + bill.sgst)
    def test_manager_discount_replaces_offers_and_is_capped(self):
        c = Cart("HSR", 1, "S1", CUST, [CartPair(prod(1, "frame", 1000000, "90031900"))], "walk_in", "upi", ManualDiscount(500, "WALKOUT", "S104"))
        b = price_cart(c, OFFERS, RATES, DAY); self.assertEqual(b.discount, 50000); self.assertEqual([e["offer_id"] for e in b.evaluations if e["applied"]], ["MANAGER_OVERRIDE"])
        c.manual = ManualDiscount(1001, "X", "S104")
        with self.assertRaises(BlockedError): price_cart(c, OFFERS, RATES, DAY)

class RxTests(unittest.TestCase):
    def codes(self, rx, ls, on=DAY, matrix=()): return [i["code"] for i in validate_lens(rx, prod(2, "lens", 1, "90015000", ls), on, list(matrix))]
    def test_in_range_ok(self): self.assertEqual(self.codes(RX, lens()), [])
    def test_sph_out_of_range_suggests_alternatives(self):
        rx = Rx("R", "in_store", date(2026, 7, 1), date(2027, 7, 1), Eye(-6.0, 0, None, None), Eye(-6.0, 0, None, None), {})
        m = [prod(i, "lens", 1, "90015000", lens("single_vision", ix, "standard", smin=lo, add=False)) for i, (ix, lo) in enumerate([(1.56, -4), (1.67, -10)])]
        iss = validate_lens(rx, m[0], DAY, m); self.assertEqual(iss[0]["code"], "SPH_OUT_OF_RANGE"); self.assertIn("1.67", iss[0]["message"]); self.assertNotIn("1.56,", iss[0]["message"].split("fit both eyes:")[1])
    def test_progressive_needs_add(self):
        rx = Rx("R", "in_store", date(2026, 7, 1), date(2027, 7, 1), Eye(-1, 0, None, None), Eye(-1, 0, None, 2.0), {}); self.assertEqual(self.codes(rx, lens()), ["ADD_MISSING"])
        self.assertEqual(self.codes(rx, lens("single_vision", add=False)), [])
    def test_cyl_limit_axis_and_expiry(self):
        rx = Rx("R", "external", date(2025, 1, 1), date(2026, 1, 1), Eye(0, -2.5, 87, None), Eye(0, 0, None, None), {})
        self.assertEqual(sorted(self.codes(rx, lens("single_vision", 1.5, "standard", -4, 4, 2, False))), ["AXIS_OFF_GRID", "CYL_OUT_OF_RANGE", "RX_EXPIRED"])
    def test_axis_warning_blocks_until_optometrist_confirms(self):
        rx = Rx("R", "external", date(2026, 1, 1), date(2027, 1, 1), Eye(0, -1.0, 87, None), Eye(0, 0, None, None), {})
        c = Cart("HSR", 1, "S1", CUST, [CartPair(prod(1, "frame", 100, "90031900"), prod(2, "lens", 100, "90015000", lens("single_vision", 1.5, "standard", -4, 4, 2, False)), rx)], "walk_in", "upi")
        self.assertEqual([i["level"] for i in check_cart(c, DAY, [], {})], ["warn"]); self.assertEqual([i["level"] for i in check_cart(c, DAY, [], {}, "S103")], ["info"])
if __name__ == "__main__": unittest.main()
