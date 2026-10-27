# Reply to the thread — Billing v1 (Shopify POS replacement)

**To:** Rohan, Meena, Dr. Iyer, Vikram, Farhan · **From:** Product-Tech (Priya's scoped plan) · **Re:** live by 15 Nov

Everyone's asks are reasonable individually; a few collide, and two are not billing. Below: what I need decided, what I'm building, what I'm not, and what I need from you.

## 1. Questions (who · why it matters for v1)

| # | To | Question | Why it matters |
|---|---|---|---|
| 1 | **Meena, Rohan** | Your pitch is *Festive 10% + second pair 50% + priority ₹500 together*. Today `FESTIVE10` is marked **non-stackable** (so is the Progressive campaign). Do you want to flip those flags (a data change, no code), or keep them exclusive? | Biggest revenue/margin lever in the pack. As configured the till picks the *best single outcome for the customer*. Floor staff will see "why didn't my offer apply" daily unless this is settled. |
| 2 | **Meena, Vikram** | Manager discount: "no approval chain" vs. "approver's identity on every manual discount". Is *a store manager entering their own PIN + reason code* enough? Is 10% on top of offers, or instead of them? | I read these as compatible (manager **is** the approver, one tap, no queue). 10% stacked on a 20% offer is a margin risk; I built it as *instead of*, and it shows what offers would have given. |
| 3 | **Vikram + CA** | (a) Frame 18% / lens 5% when sold together: is that one composite supply taxed differently? (b) How must a bill-level discount (priority ₹500) be apportioned across lines for GST? (c) Is e-invoicing/IRN applicable to us? (d) FY reset: do series restart each April, and is the credit-note series separate? (e) "To the rupee": do we compare to the paisa? | (a)/(b) change every invoice's tax; I allocate discounts pro-rata by remaining value and tax each line at its HSN, which is defensible but not confirmed. (c) would add an external call to every bill. |
| 4 | **Rohan** | "Under 10 seconds from yes": does the clock include the customer tapping UPI/card? | Terminal time is human-bound. I'm committing to **≤2 s of our own time at p95** (price + record + Shopify write); the demo bills in <0.2 s against the mock. |
| 5 | **Dr. Iyer** | Remakes: what are the 3–4 cost-bearer cases (us / customer / external optometrist), and where must cost be recorded? Who may confirm an external Rx with off-grid axis? | A remake is a zero-value order that references the parent invoice and must not touch frame stock. Cost-bearer is not a Shopify field; it needs a home (lab system?). |
| 6 | **Meena, Vikram** | Family bill: one Shopify order has one customer, and a GST invoice has one buyer. Who is the buyer when three people each have an Rx? | Lines can each carry an `rx_id`, so it's technically feasible, but refunds, credit notes and "whose lens is remade" get complicated. |
| 7 | **Farhan, Ops** | Koramangala: is the Wi-Fi drop total, or does the terminal's own SIM still work? | Decides whether we need offline cash-only mode or just a retry queue. |
| 8 | **Retail** | Policy for refunds: invoices say lenses aren't returnable once dispensed and frames within 7 days, yet Finance wants a full refund flow. Who can approve what, and within how many days? | I built the mechanics (frame restocked, lens scrapped); the rule about *when* is a business decision. |
| 9 | **Rohan** | "Priority" in the queue tool and in Shopify: same definition? | In the seed, `priority` is a Shopify tag that tracks lifetime spend > ₹5,000. If the queue tool computes it differently, the till and the queue will disagree on the same customer. |

## 2. What I'm building vs deferring

**Building (v1, Must + the cheap Shoulds Finance asked for):** customer + Rx lookup; lens validation against Rx (range, ADD, 5° axis, expiry) with actionable messages; offers by rule with best-outcome stacking; GST per line by HSN and invoice date; charge → per-store gapless invoice number → Shopify order (Rx on lens line); full refund with credit note, frame restock at billing store; day-end from Shopify with mismatch flags; cash till with variance alert (> ₹200); channel on every order and per-store channel split; audit trail with reason codes; printable invoice.

**Deferring, with reasons:** *Remake flow* and *family billing* (need answers 5, 6; both are modelling decisions that are expensive to undo). *Partial refunds* (Must is full refund; partial interacts with discount allocation). *Split payments*, *zero-value bills*. *Rohan's 10 am email* (a scheduled job on top of the day-end data; needs an owner and an email provider, so not on the till's critical path). *Offer authoring and Rx-photo AI*: not before billing is solid.

## 3. Contradictions, risks, out of scope

- **Offers vs. data:** see Q1. Also `FESTIVE10` expires **15 Nov**, the launch day, and the Progressive campaign on 31 Oct: someone must own offer calendar changes (they are data, not deploys).
- **"Fast" vs. "gapless numbers":** a gapless series needs one authority per store. With more than one till per store, local numbering would collide. v1 assumes **one till service per store**, with the series seeded from Shopify's highest existing number so we never reuse seeded invoices. A central allocator is the fix if stores run several tills.
- **"Day-end matches Shopify" vs. the till writing to Shopify after the customer pays:** during an outage a bill can be paid, numbered, and not yet in Shopify. The report shows it as a **flagged mismatch** until synced, rather than hiding it.
- **Money taken, Shopify refuses (e.g. out of stock):** the bill is marked REJECTED and a human must refund; I don't auto-refund.
- **Shopify API:** the exercise mock is REST. Shopify now steers new apps to GraphQL; the adapter boundary absorbs that, but allow time.
- **Not billing (route elsewhere):** removing the beverage question and fixing the Indiranagar footfall camera belong to the queue-tool / facilities owners. I won't fold them into this project.
- **15 Nov for all 7 stores** is the riskiest sentence in the thread (~3 weeks, cash-handling and tax unverified). I recommend **one pilot store** (with Shopify POS as fallback) the week of 2 Nov, then three, then all seven.

## 4. What I need to ship for real

Shopify: a custom app with orders/customers/inventory/products scopes, a staging store, and confirmation of API limits. Store tool: authenticated Rx and staff APIs, and a way for an optometrist to confirm an Rx on the till. Payments: terminal vendor SDK, settlement report, and its idempotency/refund-status API. Finance/CA: answers to Q3, FY prefixes, and who receives the variance alert. Ops: hardware per store (tills, printers, network), a pilot store, and a lab process for remakes. Retail: offer calendar owner, refund policy (Q8), floor training.
