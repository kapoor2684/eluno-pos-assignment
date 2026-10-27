"""Offer engine. Offers are data (offers.json). Unknown rule keys FAIL CLOSED (offer not applied, reason shown).

Semantics (documented in README, see 'Offer semantics'):
  * stackable=true offers combine with each other; stackable=false offers are exclusive (they stand alone).
  * Candidate outcomes = {all eligible stackable offers} and {each eligible exclusive offer alone}.
    We pick the candidate that gives the customer the biggest discount (tie: fewer offers, then offer ids).
  * Inside a candidate offers apply in ascending `priority`; percent offers act on the line's *remaining* value.
  * Discounts never take a line below zero; flat discounts are allocated across target lines by largest remainder.
"""
from datetime import date
from typing import Dict, List
from .money import allocate, div_half_up

LINE_CONDS = {"lens_type", "index_min", "coating"}
KNOWN = LINE_CONDS | {"min_pairs_in_cart", "customer_tag", "has_frame_and_lens"}

def _line_ok(cond, ln):
    L = ln["lens"]
    if "lens_type" in cond and not (L and L.lens_type == cond["lens_type"]): return False
    if "index_min" in cond and not (L and L.index >= cond["index_min"]): return False
    if "coating" in cond and not (L and L.coating == cond["coating"]): return False
    return True

def _fmt(p): return f"₹{p/100:,.2f}"

def _groups(offer, lines, tags):
    """-> (groups_of_line_indexes, reason_if_ineligible)"""
    r = offer["rule"]; cond = r.get("condition", {}); scope = r["applies_to"]
    bad = set(cond) - KNOWN
    if bad: return None, f"unsupported condition {sorted(bad)}"
    if scope not in ("frame", "lens", "cart", "pair"): return None, f"unsupported scope {scope}"
    if "customer_tag" in cond and cond["customer_tag"] not in tags: return None, f"customer lacks tag '{cond['customer_tag']}'"
    pairs = {l["pair"] for l in lines}
    if "min_pairs_in_cart" in cond and len(pairs) < cond["min_pairs_in_cart"]: return None, f"needs {cond['min_pairs_in_cart']} pairs, cart has {len(pairs)}"
    lc = {k: v for k, v in cond.items() if k in LINE_CONDS}
    if scope == "frame":
        idx = [l["i"] for l in lines if l["type"] == "frame" and not lc]
        return ([[i] for i in idx], None) if idx else (None, "no matching frame line")
    if scope == "lens":
        idx = [l["i"] for l in lines if l["type"] == "lens" and _line_ok(lc, l)]
        if idx and r["discount"].get("on") == "lowest_priced_lens_line":
            idx = [min(idx, key=lambda i: (lines[i]["gross"], -lines[i]["pair"]))]
        return ([[i] for i in idx], None) if idx else (None, "no lens line matches the rule")
    if scope == "cart": return ([[l["i"] for l in lines]], None) if lines else (None, "empty cart")
    groups = []
    for p in sorted(pairs):
        ls = [l for l in lines if l["pair"] == p]
        if cond.get("has_frame_and_lens") and not ({"frame", "lens"} <= {l["type"] for l in ls}): continue
        groups.append([l["i"] for l in ls])
    return (groups, None) if groups else (None, "no pair has both frame and lens")

def _apply(offer, groups, remaining) -> Dict[int, int]:
    d = offer["rule"]["discount"]; out: Dict[int, int] = {}
    if d["type"] == "percent":
        for g in groups:
            for i in g: out[i] = div_half_up(remaining[i] * d["value_bps"], 10000)
    elif d["type"] == "flat":
        for g in groups:
            amt = min(d["value_paise"], sum(remaining[i] for i in g))
            for i, a in zip(g, allocate(amt, [remaining[i] for i in g])): out[i] = a
    else:
        return {}
    cap = offer["rule"].get("max_discount_paise")
    if cap is not None and sum(out.values()) > cap:
        keys = list(out); out = dict(zip(keys, allocate(cap, [out[k] for k in keys])))
    return {i: a for i, a in out.items() if a > 0}

def _run(chosen, groups_by_id, lines):
    remaining = {l["i"]: l["gross"] for l in lines}; res = {}
    for o in sorted(chosen, key=lambda o: (o["priority"], o["offer_id"])):
        part = _apply(o, groups_by_id[o["offer_id"]], remaining)
        for i, a in part.items(): remaining[i] -= a
        res[o["offer_id"]] = part
    return res

def evaluate(offers: List[dict], lines: List[dict], tags, on: date):
    """lines: [{i, pair, type, gross, lens}]. Returns (allocation {offer_id: {line_i: paise}}, evaluations[])."""
    evals, elig, groups = {}, [], {}
    for o in offers:
        oid = o["offer_id"]
        if not o.get("active", True): evals[oid] = (False, "inactive"); continue
        if not (date.fromisoformat(o["valid_from"]) <= on <= date.fromisoformat(o["valid_to"])): evals[oid] = (False, f"outside {o['valid_from']}..{o['valid_to']}"); continue
        if "requires" in o["rule"]: evals[oid] = (False, "manual offer: needs a manager, reason code (use manual discount)"); continue
        g, why = _groups(o, lines, tags)
        if g is None: evals[oid] = (False, why); continue
        groups[oid] = g; elig.append(o)
    stack = [o for o in elig if o.get("stackable")]
    cands = ([stack] if stack else []) + [[o] for o in elig if not o.get("stackable")]
    best, best_key = [], (0, 0, "")
    scored = []
    for c in cands:
        res = _run(c, groups, lines); tot = sum(sum(p.values()) for p in res.values())
        scored.append((c, res, tot))
        key = (-tot, len(c), ",".join(sorted(o["offer_id"] for o in c)))
        if tot > 0 and (not best or key < best_key): best, best_key = (c, res, tot), key
    chosen_res = best[1] if best else {}
    chosen_ids = {o["offer_id"] for o in best[0]} if best else set()
    chosen_tot = best[2] if best else 0
    out = []
    for o in offers:
        oid = o["offer_id"]
        if oid in chosen_ids:
            amt = sum(chosen_res[oid].values())
            if amt > 0: out.append({"offer_id": oid, "applied": True, "amount": amt, "why": "stackable, combined" if o.get("stackable") and len(chosen_ids) > 1 else "best outcome for customer"})
            continue
        if oid in evals: out.append({"offer_id": oid, "applied": False, "amount": 0, "why": evals[oid][1]}); continue
        alone = next((t for c, _, t in scored if len(c) == 1 and c[0]["offer_id"] == oid), None)
        if alone is None: alone = sum(sum(p.values()) for p in _run([o], groups, lines).values())
        why = f"eligible but not combinable (non-stackable); would give {_fmt(alone)} vs {_fmt(chosen_tot)} chosen" if not o.get("stackable") else "eligible but gave nothing (already covered)"
        out.append({"offer_id": oid, "applied": False, "amount": 0, "why": why})
    alloc = {oid: part for oid, part in chosen_res.items() if sum(part.values()) > 0}
    return alloc, out
