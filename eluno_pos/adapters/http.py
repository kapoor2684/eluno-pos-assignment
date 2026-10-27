import json, time, urllib.request, urllib.error, socket

class HttpError(Exception):
    def __init__(self, status, body): self.status, self.body = status, body; super().__init__(f"HTTP {status}: {body}")
class Ambiguous(Exception):
    """The request may or may not have been processed (timeout, connection drop, 5xx). Callers of non-idempotent writes must check before retrying."""

class Http:
    def __init__(self, base, timeout=10, sleep=time.sleep, max_tries=7):
        self.base, self.timeout, self.sleep, self.max_tries = base.rstrip("/"), timeout, sleep, max_tries
    def _once(self, method, path, body):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r: return r.status, json.loads(r.read() or b"{}"), r.headers
        except urllib.error.HTTPError as e:
            raw = e.read(); 
            try: data = json.loads(raw or b"{}")
            except ValueError: data = {"raw": raw.decode(errors="replace")}
            return e.code, data, e.headers
        except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as e:
            raise Ambiguous(str(e))
    def request(self, method, path, body=None, idempotent=None):
        """429 is always retried (server refused the call, honouring Retry-After). 503/network errors are retried only when
        the call is idempotent (GETs, terminal charge keyed by order_ref); otherwise Ambiguous is raised to the caller."""
        idempotent = (method == "GET") if idempotent is None else idempotent
        for attempt in range(self.max_tries):
            try:
                code, data, hdr = self._once(method, path, body)
            except Ambiguous:
                if not idempotent or attempt == self.max_tries - 1: raise
                self.sleep(min(2 ** attempt * 0.25, 4)); continue
            if code == 429:
                self.sleep(min(float(hdr.get("Retry-After", 1) or 1), 5)); continue
            if code >= 500:
                if not idempotent: raise Ambiguous(f"HTTP {code}")
                self.sleep(min(2 ** attempt * 0.25, 4)); continue
            if code >= 400: raise HttpError(code, data)
            return data
        raise Ambiguous("retries exhausted")
