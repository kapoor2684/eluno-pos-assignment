"""POS-owned persistence (SQLite): gapless invoice series, bill journal (outbox), refund journal, cash till, audit trail."""
import json, sqlite3
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
def now(): return datetime.now(IST)

SCHEMA = """
CREATE TABLE IF NOT EXISTS series(prefix TEXT PRIMARY KEY, last_no INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invoices(invoice_no TEXT PRIMARY KEY, kind TEXT, ref TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS bills(bill_id TEXT PRIMARY KEY, store_code TEXT, state TEXT, invoice_no TEXT, charge_ref TEXT, order_ref INTEGER,
  total INTEGER, method TEXT, draft TEXT, maybe_sent INTEGER DEFAULT 0, error TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS refunds(refund_id TEXT PRIMARY KEY, bill_id TEXT, state TEXT, credit_note_no TEXT, amount INTEGER, method TEXT,
  reason TEXT, staff_id TEXT, terminal_ref TEXT, shopify_ref INTEGER, store_code TEXT, order_ref INTEGER, charge_ref TEXT, maybe_sent INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS till_sessions(session_id TEXT PRIMARY KEY, store_code TEXT, opened_at TEXT, opened_by TEXT, float INTEGER,
  closed_at TEXT, closed_by TEXT, expected INTEGER, counted INTEGER, variance INTEGER);
CREATE TABLE IF NOT EXISTS till_movements(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, type TEXT, amount INTEGER, ref TEXT UNIQUE, at TEXT, reason TEXT, by TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, who TEXT, what TEXT, ref TEXT, reason_code TEXT, detail TEXT);
"""

class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, isolation_level=None); self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
        self.db.executescript(SCHEMA)

    # ---- invoice series: gapless per prefix. A number is allocated only once money is captured, inside one txn. ----
    def allocate(self, prefix, kind, ref, bootstrap_floor):
        """bootstrap_floor(): highest number already used in the system of record; called only when the series is new."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT last_no FROM series WHERE prefix=?", (prefix,)).fetchone()
            last = row["last_no"] if row else bootstrap_floor()
            n = last + 1; no = f"{prefix}{n:05d}"
            self.db.execute("INSERT OR REPLACE INTO series VALUES(?,?)", (prefix, n))
            self.db.execute("INSERT INTO invoices VALUES(?,?,?,?,?)", (no, kind, ref, "issued", now().isoformat(timespec="seconds")))
            self.db.execute("COMMIT"); return no
        except Exception:
            self.db.execute("ROLLBACK"); raise
    def cancel_invoice(self, no): self.db.execute("UPDATE invoices SET status='cancelled' WHERE invoice_no=?", (no,))

    # ---- bills ----
    def new_bill(self, bill_id, store_code, total, method, draft):
        t = now().isoformat(timespec="seconds")
        self.db.execute("INSERT INTO bills(bill_id,store_code,state,total,method,draft,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (bill_id, store_code, "PRICED", total, method, json.dumps(draft), t, t))
    def update_bill(self, bill_id, **kw):
        kw["updated_at"] = now().isoformat(timespec="seconds"); cols = ",".join(f"{k}=?" for k in kw)
        self.db.execute(f"UPDATE bills SET {cols} WHERE bill_id=?", (*kw.values(), bill_id))
    def bill(self, bill_id):
        r = self.db.execute("SELECT * FROM bills WHERE bill_id=?", (bill_id,)).fetchone(); return dict(r) if r else None
    def bill_by_invoice(self, no):
        r = self.db.execute("SELECT * FROM bills WHERE invoice_no=?", (no,)).fetchone(); return dict(r) if r else None
    def pending(self): return [dict(r) for r in self.db.execute("SELECT * FROM bills WHERE state IN ('CHARGED','NUMBERED') ORDER BY created_at")]
    def bills_for(self, store_code, day):
        return [dict(r) for r in self.db.execute("SELECT * FROM bills WHERE store_code=? AND substr(created_at,1,10)=? AND state!='FAILED'", (store_code, day))]

    # ---- refunds ----
    def new_refund(self, **kw):
        kw["created_at"] = now().isoformat(timespec="seconds"); cols = ",".join(kw)
        self.db.execute(f"INSERT INTO refunds({cols}) VALUES({','.join('?'*len(kw))})", tuple(kw.values()))
    def update_refund(self, rid, **kw):
        self.db.execute(f"UPDATE refunds SET {','.join(f'{k}=?' for k in kw)} WHERE refund_id=?", (*kw.values(), rid))
    def refund_for_bill(self, bill_id):
        r = self.db.execute("SELECT * FROM refunds WHERE bill_id=?", (bill_id,)).fetchone(); return dict(r) if r else None
    def refund(self, rid):
        r = self.db.execute("SELECT * FROM refunds WHERE refund_id=?", (rid,)).fetchone(); return dict(r) if r else None
    def pending_refunds(self): return [dict(r) for r in self.db.execute("SELECT * FROM refunds WHERE state NOT IN ('DONE','FAILED')")]

    # ---- cash till ----
    def open_session(self, store_code): 
        r = self.db.execute("SELECT * FROM till_sessions WHERE store_code=? AND closed_at IS NULL", (store_code,)).fetchone(); return dict(r) if r else None
    def sessions_on(self, store_code, day):
        return [dict(r) for r in self.db.execute("SELECT * FROM till_sessions WHERE store_code=? AND substr(opened_at,1,10)=?", (store_code, day))]
    def movements(self, session_id): return [dict(r) for r in self.db.execute("SELECT * FROM till_movements WHERE session_id=? ORDER BY id", (session_id,))]
    def add_movement(self, session_id, type_, amount, ref, reason, by):
        self.db.execute("INSERT OR IGNORE INTO till_movements(session_id,type,amount,ref,at,reason,by) VALUES(?,?,?,?,?,?,?)", (session_id, type_, amount, ref, now().isoformat(timespec="seconds"), reason, by))

    # ---- audit ----
    def audit(self, who, what, ref=None, reason_code=None, **detail):
        self.db.execute("INSERT INTO audit(at,who,what,ref,reason_code,detail) VALUES(?,?,?,?,?,?)", (now().isoformat(timespec="seconds"), who, what, ref, reason_code, json.dumps(detail, default=str)))
