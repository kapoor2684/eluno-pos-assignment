from .http import Http

class TerminalAdapter:
    def __init__(self, http: Http): self.http = http
    def charge(self, amount_paise, method, order_ref):
        r = self.http.request("POST", "/terminal/charges", {"amount_paise": amount_paise, "method": method, "order_ref": order_ref}, idempotent=True)
        if r["status"] != "captured": raise RuntimeError(f"terminal status {r['status']}")
        return r["charge_id"]
    def refund(self, charge_id, amount_paise, reason):
        return self.http.request("POST", "/terminal/refunds", {"charge_id": charge_id, "amount_paise": amount_paise, "reason": reason}, idempotent=False)["refund_id"]
    def refunded_paise(self, charge_id):
        return self.http.request("GET", f"/terminal/charges/{charge_id}")["refunded_paise"]
