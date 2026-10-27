from datetime import date
from urllib.parse import quote
from ..domain import Rx, Eye
from .http import Http

def _eye(e): return Eye(e["sph"], e["cyl"], e["axis"], e["add"])

class SmtAdapter:
    def __init__(self, http: Http): self.http = http
    def prescriptions(self, phone):
        r = self.http.request("GET", f"/smt/prescriptions?phone={quote(phone)}")["prescriptions"]
        return [Rx(x["rx_id"], x["source"], date.fromisoformat(x["date"]), date.fromisoformat(x["valid_until"]), _eye(x["od"]), _eye(x["os"]), x["pd"]) for x in r]
    def staff(self, store_code):
        return self.http.request("GET", f"/smt/staff?store={quote(store_code)}")["staff"]
