"""CLI over the use-cases. Env: ELUNO_BASE (default http://localhost:8787), ELUNO_DB (default .pos/pos.db), ELUNO_DATA (default ./data)."""
import argparse, json, os, sys, time
from datetime import date
from .adapters.http import Http, HttpError, Ambiguous
from .adapters.shopify import ShopifyAdapter
from .adapters.smt import SmtAdapter
from .adapters.terminal import TerminalAdapter
from .domain import BlockedError
from .invoice import render
from .money import inr
from .service import Pos, PendingSync
from .store import Store
from .tax import RateTable

def make_pos():
    data = os.environ.get("ELUNO_DATA", "data"); db = os.environ.get("ELUNO_DB", ".pos/pos.db")
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True); http = Http(os.environ.get("ELUNO_BASE", "http://localhost:8787"))
    return Pos(ShopifyAdapter(http), SmtAdapter(http), TerminalAdapter(http), Store(db), json.load(open(f"{data}/offers.json")), RateTable.load(f"{data}/tax_rates.json"))

def show_bill(bill, issues=()):
    for e in bill.evaluations: print(f"  offer {e['offer_id']:<18}{'APPLIED ' + inr(e['amount']) if e['applied'] else 'no':<20}{e['why']}")
    for l in bill.lines:
        print(f"  {l.product.sku:<8}{l.product.title[:34]:<35}{inr(l.gross):>12} -{inr(l.discount):>10}  taxable {inr(l.taxable):>11}  CGST {inr(l.cgst):>9}  SGST {inr(l.sgst):>9}  = {inr(l.net)}")
    print(f"  TOTAL {inr(bill.total)}  (gross {inr(bill.gross)}, discount {inr(bill.discount)}, taxable {inr(bill.taxable)}, CGST {inr(bill.cgst)}, SGST {inr(bill.sgst)})")
    for i in issues:
        if i["level"] == "info": print("  note:", i["message"])

def main(argv=None):
    ap = argparse.ArgumentParser(prog="pos"); sub = ap.add_subparsers(dest="cmd", required=True)
    def cart_args(p):
        p.add_argument("--store", required=True); p.add_argument("--staff", required=True); p.add_argument("--phone", required=True)
        p.add_argument("--pair", action="append", required=True, help="FRAME_SKU:LENS_SKU  (either side may be empty)")
        p.add_argument("--channel", default="walk_in"); p.add_argument("--pay", default="upi")
        p.add_argument("--manual-pct", type=float); p.add_argument("--reason-code"); p.add_argument("--approver")
        p.add_argument("--confirm-rx", help="optometrist staff id confirming an Rx warning (e.g. off-grid axis)")
    p = sub.add_parser("lookup"); p.add_argument("--phone", required=True)
    cart_args(sub.add_parser("quote")); cart_args(sub.add_parser("bill"))
    p = sub.add_parser("refund"); p.add_argument("--invoice", required=True); p.add_argument("--reason", required=True); p.add_argument("--staff", required=True)
    p = sub.add_parser("dayend"); p.add_argument("--store", required=True); p.add_argument("--date", required=True); p.add_argument("--json", action="store_true")
    sub.add_parser("resume")
    p = sub.add_parser("invoice"); p.add_argument("--invoice", required=True)
    t = sub.add_parser("till").add_subparsers(dest="till_cmd", required=True)
    p = t.add_parser("open"); p.add_argument("--store", required=True); p.add_argument("--staff", required=True); p.add_argument("--float", type=float, required=True)
    for k in ("in", "out"):
        p = t.add_parser(k); p.add_argument("--store", required=True); p.add_argument("--staff", required=True); p.add_argument("--amount", type=float, required=True); p.add_argument("--reason", required=True)
    p = t.add_parser("close"); p.add_argument("--store", required=True); p.add_argument("--staff", required=True); p.add_argument("--counted", type=float, required=True)
    a = ap.parse_args(argv); pos = make_pos(); P = lambda r: int(round(r * 100)); t0 = time.time()
    try:
        if a.cmd == "lookup":
            c, rx, all_rx = pos.lookup(a.phone)
            if not c: print("No customer with that phone."); return 1
            flags = [f for f in sorted(c.tags) if f == "priority" or f.startswith("campaign:")]
            print(f"{c.name}  {c.phone}  tags={sorted(c.tags)}" + (f"   ** FLAGS: {', '.join(flags)} **" if flags else ""))
            for r in all_rx: print(f"  {r.rx_id} {r.date} {r.source} OD {r.od.sph:+.2f}/{r.od.cyl:+.2f}x{r.od.axis} add {r.od.add}  OS {r.os.sph:+.2f}/{r.os.cyl:+.2f}x{r.os.axis} add {r.os.add}  valid until {r.valid_until}{'  [LATEST VALID]' if rx and r.rx_id == rx.rx_id else ''}")
            if not rx: print("  No valid prescription.")
        elif a.cmd in ("quote", "bill"):
            pairs = [tuple((x or None) for x in (s.split(":") + [""])[:2]) for s in a.pair]
            manual = {"bps": int(round(a.manual_pct * 100)), "reason_code": a.reason_code, "approver_id": a.approver} if a.manual_pct else None
            cart = pos.build_cart(a.store, a.staff, a.phone, pairs, a.channel, a.pay, manual)
            fl = [f for f in sorted(cart.customer.tags) if f == "priority" or f.startswith("campaign:")]
            print(f"Customer: {cart.customer.name}" + (f"   ** {', '.join(fl)} **" if fl else ""))
            if a.cmd == "quote":
                bill, issues = pos.quote(cart, a.confirm_rx); show_bill(bill, issues)
            else:
                bill, issues = pos.quote(cart, a.confirm_rx); show_bill(bill, issues)
                row = pos.checkout(cart, a.confirm_rx)
                print(f"\nBILLED {row['invoice_no']}  Shopify order {row['order_ref']}  state={row['state']}  in {time.time()-t0:.1f}s")
        elif a.cmd == "refund":
            r = pos.refund_full(a.invoice, a.reason, a.staff); print(f"REFUNDED {a.invoice}: credit note {r['credit_note_no']}  {inr(r['amount'])}  state={r['state']}")
        elif a.cmd == "resume":
            print(f"resumed {len(pos.resume())} bill(s); pending now: {len(pos.s.pending())} bills, {len(pos.s.pending_refunds())} refunds")
        elif a.cmd == "invoice":
            b = pos.s.bill_by_invoice(a.invoice); print(render(json.loads(b["draft"]), a.invoice, f"order {b['order_ref']}" if b["order_ref"] else None))
        elif a.cmd == "dayend":
            rep = pos.day_end(a.store, date.fromisoformat(a.date))
            if a.json: print(json.dumps(rep, indent=1)); return 0
            print(f"DAY-END {a.store} {rep['date']}  bills={rep['bill_count']}  gross={inr(rep['gross'])}  discounts={inr(rep['discounts'])}  net={inr(rep['net_sales'])}  taxable={inr(rep['taxable'])}  tax={inr(rep['tax'])}")
            print(f"  refunds={inr(rep['refunds'])} ({rep['refund_count']}; tax reversed {inr(rep['refund_tax_reversed'])})")
            for m, v in rep["by_payment_method"].items(): print(f"  {m:<6} sales {inr(v['sales']):>12}  refunds {inr(v['refunds']):>12}  net {inr(v['net']):>12}")
            for c, v in rep["by_channel"].items(): print(f"  channel {c:<16} bills {v['bills']}  net {inr(v['net'])}")
            for s in rep["till_sessions"]: print(f"  till {s['session_id']}: expected {s['expected']}, counted {s['counted']}, variance {s['variance']}" + ("  ** ALERT FINANCE **" if s["alert_finance"] else ""))
            print("  MISMATCHES: " + ("none" if not rep["mismatches"] else "")); [print("   !", m) for m in rep["mismatches"]]
        elif a.cmd == "till":
            if a.till_cmd == "open": print("opened", pos.till_open(a.store, a.staff, P(a.float)))
            elif a.till_cmd == "close": r = pos.till_close(a.store, a.staff, P(a.counted)); print(f"closed {r['session_id']}: expected {inr(r['expected'])} counted {inr(r['counted'])} variance {inr(r['variance'])}" + ("  ** variance > ₹200: finance alerted **" if r["alert_finance"] else ""))
            else: print("recorded", pos.till_move(a.store, a.staff, "paid_in" if a.till_cmd == "in" else "paid_out", P(a.amount), a.reason))
    except BlockedError as e:
        print("BLOCKED:"); [print(f"  - [{i.get('code')}] {i['message']}") for i in e.issues]; return 2
    except PendingSync as e:
        print("PAID, SYNC PENDING:", e); return 3
    except (HttpError, Ambiguous) as e:
        print("ERROR talking to a system:", e); return 4
    return 0

if __name__ == "__main__": sys.exit(main())
