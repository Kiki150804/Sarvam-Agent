"""Document Intelligence Agent for Indian-language business documents (Sarvam AI).
extract -> structure (SQLite/CSV) -> rules/alerts -> dashboard.  Stdlib only (+ optional pypdf)."""
import csv, glob, json, os, re, sqlite3, sys, urllib.request, html

APPROVAL_LIMIT = 50_000   # INR: invoices above this need approval
MODEL = "sarvam-105b"
URL = "https://api.sarvam.ai/v1/chat/completions"

def load_key():
    key = os.environ.get("SARVAM_API_KEY")
    if not key and os.path.exists(".env"):
        for line in open(".env"):
            if line.startswith("SARVAM_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    return key

PROMPT = """You extract data from Indian business documents written in any Indian language
(Hindi, Tamil, Bengali, Telugu, Marathi, Kannada, Gujarati, Malayalam, English...).
Return ONLY a JSON object, no prose, with exactly these keys:
language, doc_type (invoice|receipt|bill|other), vendor, invoice_no, date (YYYY-MM-DD),
gstin (15-char GST number or null), total_amount (number in INR, no commas), gst_amount (number or null),
summary_english (one sentence in English).
Use null when a value is missing. Convert Indic numerals (१२३, ௧௨௩) to digits."""

def read_doc(path):
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    return open(path, encoding="utf-8").read()

def ask_sarvam(text, key):
    body = json.dumps({"model": MODEL, "temperature": 0, "max_tokens": 8000,
        "messages": [{"role": "system", "content": PROMPT},
                     {"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json",
                                             "api-subscription-key": key})
    with urllib.request.urlopen(req, timeout=300) as r:
        msg = json.load(r)["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content") or ""

def parse_json(reply):
    reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
    m = re.search(r"\{.*\}", reply, flags=re.S)
    if not m: raise ValueError("no JSON in model reply")
    return json.loads(m.group(0))

GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]$")

def check_rules(d):
    """Workflow automation: returns list of alerts."""
    alerts = []
    amt = d.get("total_amount")
    if isinstance(amt, (int, float)) and amt > APPROVAL_LIMIT:
        alerts.append(f"APPROVAL REQUIRED: Rs {amt:,.0f} exceeds Rs {APPROVAL_LIMIT:,}")
    g = d.get("gstin")
    if not g: alerts.append("MISSING GSTIN")
    elif not GSTIN.match(g): alerts.append(f"INVALID GSTIN FORMAT: {g}")
    if amt is None: alerts.append("TOTAL NOT FOUND - manual review")
    return alerts

def save(db, name, d, alerts):
    db.execute("""CREATE TABLE IF NOT EXISTS docs(file TEXT PRIMARY KEY, language, doc_type, vendor,
        invoice_no, date, gstin, total_amount REAL, gst_amount REAL, summary_english, alerts)""")
    db.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (name, d.get("language"), d.get("doc_type"), d.get("vendor"), d.get("invoice_no"),
         d.get("date"), d.get("gstin"), d.get("total_amount"), d.get("gst_amount"),
         d.get("summary_english"), "; ".join(alerts)))
    db.commit()

def dashboard(db):
    rows = db.execute("SELECT * FROM docs ORDER BY total_amount DESC").fetchall()
    cols = [c[1] for c in db.execute("PRAGMA table_info(docs)")]
    with open("docs.csv", "w", newline="", encoding="utf-8-sig") as f:   # BI-ready
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    total = sum(r[7] or 0 for r in rows); flagged = sum(1 for r in rows if r[10])
    trs = "".join("<tr class='%s'>%s</tr>" % ("bad" if r[10] else "", "".join(
        f"<td>{html.escape(str(x if x is not None else '-'))}</td>" for x in
        (r[0], r[1], r[3], r[5], f"{r[7]:,.0f}" if r[7] is not None else None, r[10] or "OK"))) for r in rows)
    open("dashboard.html", "w", encoding="utf-8").write(f"""<meta charset=utf-8><title>DocAgent</title>
<style>body{{font:15px system-ui;margin:2rem}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:6px 10px}}
.bad{{background:#fff0f0}}.k{{display:inline-block;margin-right:2rem;font-size:1.4rem}}</style>
<h2>Document Intelligence Dashboard</h2>
<span class=k>{len(rows)} docs</span><span class=k>Rs {total:,.0f} total</span><span class=k>{flagged} flagged</span>
<table><tr><th>File<th>Language<th>Vendor<th>Date<th>Total (Rs)<th>Alerts</tr>{trs}</table>""")

def main(replies=None):
    key = None if replies else load_key()
    if not replies and not key: sys.exit("Set SARVAM_API_KEY (env var or .env file).")
    db = sqlite3.connect("docs.db")
    for path in sorted(glob.glob("docs/*")):
        name = os.path.basename(path)
        try:
            reply = replies[name] if replies else ask_sarvam(read_doc(path), key)
            try:
                d = parse_json(reply)
            except Exception:
                open(f"debug_{name}", "w", encoding="utf-8").write(reply or "(empty reply)")
                raise ValueError(f"no JSON; raw reply saved to debug_{name} ({len(reply or '')} chars)")
            alerts = check_rules(d); save(db, name, d, alerts)
            print(f"{name:22} {d.get('language')!s:10} Rs {d.get('total_amount')}  {alerts or 'OK'}")
        except Exception as e:
            print(f"{name:22} FAILED: {e}")
    dashboard(db); print("-> docs.db, docs.csv, dashboard.html")

if __name__ == "__main__":
    main()
