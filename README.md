# Eluno till (v1): Shopify POS replacement

Python 3 standard library only (written and tested on 3.12; no syntax newer than 3.8). Shopify stays the system of record; this app prices, charges, numbers and writes everything to Shopify through an adapter.

## Run it

```
./demo.sh                                        # starts the flaky mock (429/503 ON), fresh DB, runs the whole Must flow
python3 -m unittest discover -s tests -t .       # 31 tests (unit + integration against the real mock, in-process)
```

Manual use: `./run_mock.sh` (FLAKE=0 for a calm mock), then `./pos --help`:

```
./pos lookup --phone +919000000005
./pos quote  --store HSR --staff S105 --phone +919000000005 --pair FR-0003:LN-0043        # --pair FRAME:LENS, repeatable, either side optional
./pos bill   ... --pay upi|card|cash --channel walk_in|whatsapp_booked|online_pickup [--manual-pct 5 --reason-code WALKOUT --approver S104] [--confirm-rx S103]
./pos refund --invoice ELU/HSR/FY26/00003 --reason CHANGED_MIND --staff S105
./pos dayend --store HSR --date 2026-10-26
./pos till open|in|out|close ...      ./pos resume      ./pos invoice --invoice ...
```
Restarting the mock resets Shopify; delete `.pos/` too (`demo.sh` does both). `STAKEHOLDER_REPLY.md` is Part A.

## Architecture

```
cli.py ─> service.py (use-cases: checkout / refund / resume / till / day-end)
            │  core (no Shopify types):  domain.py  pricing.py  offers.py  tax.py  rx.py  money.py  report.py  invoice.py
            │  ports.py (Commerce, Prescriptions, Terminal)
            └─ adapters/: shopify.py (the only file that knows Shopify JSON) · smt.py · terminal.py · http.py (retry policy)
store.py: SQLite = invoice series, bill/refund journal, till, audit
```
*If Shopify is replaced in 2027*: write one new `Commerce` adapter (create_order, get_order, orders, refund_order, catalog, stock, customer). The core only sees `Product`, `Customer`, `PlacedOrder`, and plain-dict drafts.

## Decisions worth knowing

- **Money**: integer paise inside; decimal strings only in `shopify.py`.
- **Tax**: rate chosen by HSN **and invoice date** from `tax_rates.json`; `taxable = round_half_up(gross·10000/(10000+bps))`, CGST = half of tax rounded half-up, SGST = remainder, per line, after discounts. A test replays every seed order line and matches Shopify's tax to the paisa. Refunds reverse the tax **recorded on the original line**, so a later rate change never alters an old refund.
- **Offer semantics** (data in `offers.json`, unknown rule keys fail closed): `stackable:true` offers combine; `stackable:false` offers stand alone. The engine evaluates {all stackable} and {each exclusive alone} and **picks what is best for the customer** (tie: fewer offers). Inside a combo, offers apply in `priority` order, percentages act on the line's *remaining* value, flat amounts are allocated across target lines by largest remainder, caps and "never below zero" are enforced. Each offer gets `{applied, amount, why}`; the quote shows why a non-applied offer lost. Manager discount (≤ cap in `offers.json`, reason code + manager approver) **replaces** automatic offers and shows what they would have given. *On the seed, Karthik's bill gets ₹1,800 off vs the old system's ₹1,424.85.*
- **Invoice numbers**: allocated in one SQLite transaction **after payment is captured**, so a declined card burns no number. The series bootstraps from the highest number already in Shopify (seed data already has `…/00001`). Credit notes have their own series (`…/CN/nnnnn`), allocated after the money moves.
- **Idempotency**: key = `bill_id` (UUID) written to the journal **before** any money moves. It is the terminal `order_ref` (terminal dedupes) and a Shopify `note_attribute`. Shopify create isn't idempotent, so after any ambiguous failure (timeout, 5xx, process crash) the adapter first looks the order up by key and only POSTs if absent. `429` is always retried honouring `Retry-After`. Refunds have the same key pattern; the terminal refund checks `refunded_paise` before re-sending.
- **Journal states** `PRICED → CHARGED → NUMBERED → SYNCED` (refunds `PENDING → MONEY_OUT → NUMBERED → DONE`). A paid bill never vanishes; `./pos resume` drives pending ones forward and is safe to run repeatedly.
- **Day-end source of truth**: Shopify. The till's journal and cash drawer are cross-checked and any difference (unsynced bill, amount mismatch, cash drawer vs Shopify cash) is printed as a mismatch rather than silently trusted. "Gross" = MRP before discounts; taxable + tax = net sales; refunds count on the day they are processed.
- **Prescriptions**: latest *valid* Rx; expired ⇒ blocked with the expiry date. Lens range, ADD, cylinder, missing axis block with a message that names the fix (and lists indices that *do* fit). Off-5° axis (external Rx) blocks until an **optometrist** passes `--confirm-rx`.

## What's built (Must: all 7) and Should picked

All seven Must items. Plus: cash till (float, cash sales/refunds automatic, paid-in/out with reasons, variance alert > ₹200), channel on every order + channel split in day-end, audit table (who/what/when/reason), printable text invoice. Not done: partial refund, remake, family billing, split payments, Stretch.

## Known gaps (honest list)

- **One till service per store**: SQLite is local, so two tills in one store would both allocate numbers (see reply, §3). Needs a store-level service or central allocator.
- Shopify REST list calls use `limit=250` **without pagination**; real Shopify needs `Link`-header paging and (for new apps) GraphQL. Order lookup by key scans recent orders; in production use a tag/metafield query.
- Only the HTTP contract of the mocks is tested; real terminal timeouts, auth, and Shopify rate limits (leaky bucket) are not.
- Permanent Shopify rejection after payment (4xx) marks the bill `REJECTED` and needs a manual refund; no automatic void.
- Day-end fetches the last ≥30 days of orders to find refunds; fine for the mock, wasteful at scale. Stock is checked at quote time and enforced by Shopify at create (`decrement_obeying_policy`), so a race fails loudly after payment.
- Authentication is by staff ID only (no PIN); roles come from the store tool. Manual discounts require a manager approver ID; a real build needs PIN/biometric.
- Remakes, family billing and refund-window policy are *not* guessed; see the reply for the questions.

## Where AI helped / misled (for the Loom)
Fill this in with your own experience. Things worth checking in the code regardless of who wrote it: the offer-stacking rule (a policy choice, not a fact), the order of "allocate invoice number" vs "charge", and the lookup-before-retry logic for lost responses.
