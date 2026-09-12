"""Smoke test for the 5 follow-up features.
Reuses dr1 / nurse1 / hospadmin / Test1234! from the existing suites.
"""
import json, sys, time, urllib.parse, urllib.request, urllib.error

BASE = "http://127.0.0.1:8000"
PW = "Test1234!"

def login(u, p):
    body = urllib.parse.urlencode({"username": u, "password": p}).encode()
    req = urllib.request.Request(BASE + "/login", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]

def http(method, path, *, token=None, body=None):
    h = {"Accept": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
            return r.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try: return e.code, json.loads(payload)
        except: return e.code, payload.decode("utf-8", "replace")

def must(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: sys.exit(1)

def section(t): print(f"\n=== {t} ===")

def main():
    doc   = login("dr1",       PW)
    hosp  = login("hospadmin", PW)
    n1    = login("nurse1",    PW)

    # ---- F5: nurse history endpoint ----
    section("F5: /nurse/patients/history")
    s, h = http("GET", "/nurse/patients/history", token=n1)
    must(s == 200, f"history endpoint → {s}")
    items = (h or {}).get("items", [])
    print(f"  INFO  history items = {len(items)}")
    for it in items[:3]:
        must("discharged_at" in it, "item has discharged_at field")
        must(it["discharged_at"] is not None, "item.discharged_at is non-null")

    # ---- F4: hospital dashboard endpoints ----
    section("F4: /admissions?status=discharged")
    s, d = http("GET", "/admissions?status=discharged", token=hosp)
    must(s == 200, f"discharged list → {s}")
    if d.get("items"):
        first = d["items"][0]
        must(first.get("discharged_at") is not None, "discharged item has discharged_at")
    s, a = http("GET", "/admissions?status=active", token=hosp)
    must(s == 200, f"active list → {s}")

    # ---- F3b: 409 on filing for an already-admitted patient ----
    section("F3b: 409 when filing for currently-admitted patient")
    # Find an active admission for one of dr1's patients
    s, ad = http("GET", "/admissions?status=all", token=doc)
    must(s == 200, f"/admissions?status=all → {s}")
    active = [x for x in (ad.get("items") or []) if not x.get("discharged_at")]
    if not active:
        print("  SKIP  no active admission for dr1 — can't exercise F3b")
    else:
        # Pick one of dr1's linked patients; need patient_id not admission.patient_id
        target_patient_id = active[0]["patient_id"]
        # Find a hospital id we can address
        s, hs = http("GET", "/hospitals", token=doc)
        must(s == 200, f"/hospitals → {s}")
        hid = (hs.get("items") or [{}])[0]["id"]
        s, err = http("POST", "/hospital-admit-requests", token=doc,
                      body={"patient_id": target_patient_id, "hospital_id": hid, "reason": "smoke 409 test"})
        must(s == 409, f"re-file while admitted → {s} (expect 409)")
        must("currently admitted" in (err.get("detail", "").lower() if isinstance(err, dict) else str(err).lower()),
             f"detail mentions currently admitted (got {err})")

    # ---- F3c: bulk-delete endpoint ----
    section("F3c: /hospital-admit-requests/bulk-delete")
    # First find one of dr1's terminal-state requests
    s, lst = http("GET", "/hospital-admit-requests", token=doc)
    must(s == 200, f"list admit requests → {s}")
    reqs = (lst or {}).get("items", [])
    print(f"  INFO  dr1 has {len(reqs)} request(s) total")
    pending = [r for r in reqs if r["status"] == "pending"]
    terminal = [r for r in reqs if r["status"] != "pending"]
    if not terminal:
        print("  SKIP  no terminal requests to delete")
    else:
        victim = terminal[0]["id"]
        s, out = http("POST", "/hospital-admit-requests/bulk-delete", token=doc, body={"ids": [victim]})
        must(s == 200, f"bulk delete → {s}")
        must(out.get("deleted_count", 0) >= 1, f"deleted_count >= 1 (got {out.get('deleted_count')})")
        must(victim not in (out.get("skipped_ids") or []), "deleted row not in skipped_ids")

    # ---- F3c: skip a pending row ----
    if pending:
        pid = pending[0]["id"]
        s, out = http("POST", "/hospital-admit-requests/bulk-delete", token=doc, body={"ids": [pid]})
        must(s == 200, f"bulk-delete pending → {s}")
        must(pid in (out.get("skipped_ids") or []), f"pending id {pid} in skipped_ids")
        must(out.get("deleted_count", 0) == 0, "deleted_count is 0 for pending-only input")

    # ---- F3c: not-your-row is skipped ----
    section("F3c: skip ids not owned by caller")
    s, out = http("POST", "/hospital-admit-requests/bulk-delete", token=doc, body={"ids": [999999]})
    must(s == 200, f"unknown id → {s}")
    must(999999 in (out.get("skipped_ids") or []), "unknown id in skipped_ids")
    must(out.get("deleted_count", 0) == 0, "deleted_count 0 for unknown id")

    # ---- F3c: empty list ----
    s, out = http("POST", "/hospital-admit-requests/bulk-delete", token=doc, body={"ids": []})
    must(s == 200, f"empty ids → {s}")
    must(out.get("deleted_count", 0) == 0, "empty input → deleted_count 0")

    # ---- F3c: not a doctor ----
    s, err = http("POST", "/hospital-admit-requests/bulk-delete", token=n1, body={"ids": [1]})
    must(s == 403, f"nurse denied → {s} (expect 403)")

    # ---- F1 / F2: smoke the existing AI report + chat endpoints still work ----
    section("F1/F2 regression: AI summary + chat list")
    s, acts = http("GET", "/nurse/patients/active", token=n1)
    must(s == 200, f"/nurse/patients/active → {s}")
    nitems = (acts or {}).get("items", [])
    if nitems:
        adm_id = nitems[0]["admission_id"]
        # Use the nurse token — the notes endpoint is gated to the nurse
        # currently assigned to the admission (the doctor needs an open
        # chat thread row, which is exercised by the original
        # smoke_ai_report.py suite).
        s, notes = http("GET", f"/admissions/{adm_id}/nurse-notes?limit=20", token=n1)
        must(s == 200, f"notes list → {s}")
        agg = [n for n in (notes.get("items") or []) if (n.get("kind") or "") == "report_aggregate"]
        must(len(agg) >= 1, f"at least one aggregate note present (got {len(agg)})")

    print("\nALL 5-FEATURE SMOKE STEPS PASSED ✅")

if __name__ == "__main__":
    main()
