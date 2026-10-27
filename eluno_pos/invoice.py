from .money import to_str, inr

def render(d, invoice_no, shopify_label=None):
    w = 104; out = ["=" * w, "ELUNO — TAX INVOICE".center(w), "=" * w,
        f"Invoice no : {invoice_no}    Date: {d['date']}    Store: {d['store_code']}    Staff: {d['staff_id']}",
        f"Customer   : {d['customer']}    Channel: {d['channel']}", "-" * w,
        f"{'#':<2} {'Description':<38}{'HSN':<10}{'MRP(incl)':>11}{'Discount':>10}{'Taxable':>11}{'CGST':>9}{'SGST':>9}{'Total':>11}", "-" * w]
    for n, l in enumerate(d["lines"], 1):
        disc = sum(l["discounts"].values()); desc = (l["title"] + (f" Rx {l['rx_id']}" if l.get("rx_id") else ""))[:37]
        out.append(f"{n:<2} {desc:<38}{l['hsn']:<10}{to_str(l['gross']):>11}{to_str(disc):>10}{to_str(l['taxable']):>11}{to_str(l['cgst']):>9}{to_str(l['sgst']):>9}{to_str(l['gross']-disc):>11}")
    g = sum(l["gross"] for l in d["lines"]); dc = sum(sum(l["discounts"].values()) for l in d["lines"])
    out += ["-" * w, f"{'TOTAL':<51}{to_str(g):>11}{to_str(dc):>10}{to_str(sum(l['taxable'] for l in d['lines'])):>11}{to_str(sum(l['cgst'] for l in d['lines'])):>9}{to_str(sum(l['sgst'] for l in d['lines'])):>9}{to_str(g-dc):>11}",
            "=" * w, "Offers applied : " + (", ".join(f"{o['offer_id']} ({inr(o['amount'])})" for o in d["offers"]) or "none")]
    if d.get("manual"): out.append(f"Manual discount: reason={d['manual']['reason_code']} approved by {d['manual']['approver_id']}")
    out += [f"Payment        : {d['payment_method'].upper()} {inr(d['total'])}", f"Shopify ref    : {shopify_label or 'pending sync'}", "-" * w,
            "Prices are inclusive of GST. Intra-state supply: CGST + SGST. Lenses are made to order and are not returnable once dispensed.", "=" * w]
    return "\n".join(out)
