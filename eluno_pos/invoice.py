
from .money import to_str, inr


WIDTH = 123


def amount_in_words(amount):
    """Convert a rupee amount to Indian English words."""
    n = int(round(amount))
    if n == 0:
        return "Zero rupees only"

    ones = [
        "", "One", "Two", "Three", "Four", "Five", "Six",
        "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
        "Thirteen", "Fourteen", "Fifteen", "Sixteen",
        "Seventeen", "Eighteen", "Nineteen"
    ]
    tens = [
        "", "", "Twenty", "Thirty", "Forty", "Fifty",
        "Sixty", "Seventy", "Eighty", "Ninety"
    ]

    def under_thousand(value):
        words = []

        if value >= 100:
            words.extend([ones[value // 100], "Hundred"])
            value %= 100

        if value >= 20:
            words.append(tens[value // 10])
            value %= 10

        if value:
            words.append(ones[value])

        return " ".join(words)

    parts = []

    for divisor, label in [
        (10000000, "Crore"),
        (100000, "Lakh"),
        (1000, "Thousand"),
        (1, ""),
    ]:
        group = n // divisor
        n %= divisor

        if group:
            parts.append(under_thousand(group))
            if label:
                parts.append(label)

    return " ".join(parts) + " rupees only"


def _money(value):
    return to_str(value)


def _tax_rate(taxable, tax):
    if not taxable:
        return "0%"

    rate = round((tax / taxable) * 100, 2)
    if rate.is_integer():
        return f"{int(rate)}%"
    return f"{rate:g}%"


def render(d, invoice_no, shopify_label=None):
    lines = d.get("lines", [])
    offers = d.get("offers", [])
    payment_method = d.get("payment_method", "unknown").upper()

    gross = sum(line["gross"] for line in lines)
    discount = sum(
        sum(line.get("discounts", {}).values())
        for line in lines
    )
    taxable = sum(line["taxable"] for line in lines)
    cgst = sum(line["cgst"] for line in lines)
    sgst = sum(line["sgst"] for line in lines)
    total = gross - discount

    out = [
        "=" * WIDTH,
        "ELUNO".center(WIDTH),
        str(d.get("store_name", d.get("store_code", "ELUNO"))).center(WIDTH),
        str(d.get("store_address", "")).center(WIDTH),
        "TAX INVOICE".center(WIDTH),
        "=" * WIDTH,
        f"Invoice no : {invoice_no:<35} Date : {d.get('date', '')}",
        f"Store      : {d.get('store_code', ''):<35} Staff: {d.get('staff_id', '')}",
        f"Customer   : {d.get('customer', ''):<35} Phone: {d.get('phone', '')}",
        f"Rx on file : {d.get('rx_id', 'N/A'):<35} Channel: {d.get('channel', '')}",
        "-" * WIDTH,
        (
            f"{'#':<3}{'Description':<36}{'HSN':<11}"
            f"{'Qty':>5}{'MRP(incl)':>12}{'Discount':>12}"
            f"{'Taxable':>12}{'CGST':>12}{'SGST':>12}{'Total':>12}"
        ),
        "-" * WIDTH,
    ]

    for index, line in enumerate(lines, 1):
        line_discount = sum(line.get("discounts", {}).values())
        description = line.get("title", "Item")
        if line.get("rx_id"):
            description += f" Rx {line['rx_id']}"

        description = description[:35]
        qty = line.get("quantity", 1)

        line_total = line["gross"] - line_discount
        cgst_rate = _tax_rate(line["taxable"], line["cgst"])
        sgst_rate = _tax_rate(line["taxable"], line["sgst"])

        out.append(
            f"{index:<3}{description:<36}{line.get('hsn', ''):<11}"
            f"{qty:>5}{_money(line['gross']):>12}"
            f"{_money(line_discount):>12}{_money(line['taxable']):>12}"
            f"{(cgst_rate + ' ' + _money(line['cgst'])):>12}"
            f"{(sgst_rate + ' ' + _money(line['sgst'])):>12}"
            f"{_money(line_total):>12}"
        )

    out.extend([
        "-" * WIDTH,
        (
            f"{'TOTAL':<55}{_money(gross):>12}"
            f"{_money(discount):>12}{_money(taxable):>12}"
            f"{_money(cgst):>12}{_money(sgst):>12}{_money(total):>12}"
        ),
        "=" * WIDTH,
        "Offers applied : " + (
            ", ".join(
                f"{offer['offer_id']} ({inr(offer['amount'])})"
                for offer in offers
            ) or "none"
        ),
    ])

    if d.get("manual"):
        manual = d["manual"]
        out.append(
            f"Manual discount: reason={manual.get('reason_code', '')} "
            f"approved by {manual.get('approver_id', '')}"
        )

    out.append(
        f"Payment        : {payment_method} {inr(d.get('total', total))}"
    )

    if d.get("terminal_ref"):
        out.append(f"Terminal ref   : {d['terminal_ref']}")

    out.append(
        f"Amount in words: {amount_in_words(d.get('total', total))}"
    )

    out.append(
        f"Shopify ref    : {shopify_label or d.get('shopify_ref', 'pending sync')}"
    )

    out.extend([
        "-" * WIDTH,
        "Prices are inclusive of GST. Intra-state supply: CGST + SGST.",
        (
            "Taxable value is calculated from the inclusive price less tax, "
            "per line."
        ),
        (
            "Lenses are made to order against the prescription on file and "
            "are not returnable once dispensed; frames may be returned "
            "within 7 days."
        ),
        "=" * WIDTH,
    ])

    return "\n".join(out)
