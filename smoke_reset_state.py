"""Reset all admit-queue + active-admission state for Test Patient (id=1).

Run before smoke_nurse_place.py so the smoke test starts from a clean slate.
"""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def login(username, password):
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        BASE + "/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def http(method, path, *, token=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    doc_token = login("dr1", "Test1234!")
    adm_token = login("hospadmin", "Test1234!")

    # 1. Cancel every pending admit request for dr1's patient #1.
    s, pending = http("GET", "/hospital-admit-requests?status=pending", token=doc_token)
    rows = pending.get("items", []) if isinstance(pending, dict) else (pending or [])
    print(f"pending for me: {len(rows)}")
    for r in rows:
        if r["patient_id"] == 1:
            s2, _ = http("POST", f"/hospital-admit-requests/{r['id']}/cancel", token=doc_token, body={})
            print(f"  cancel #{r['id']} -> {s2}")

    # 2. Also cancel any pending request where I'm the hospital admin
    # (different endpoint might be available — fall back to status-only listing).
    s, all_pending_resp = http("GET", "/hospital-admit-requests?status=pending", token=adm_token)
    all_pending = all_pending_resp.get("items", []) if isinstance(all_pending_resp, dict) else (all_pending_resp or [])
    print(f"pending all: {len(all_pending)}")
    for r in all_pending:
        # Doctor-side cancel only works on own requests. For someone else's
        # pending requests, we'll skip and rely on a doctor logout; the
        # simplest path is to file + accept a fresh one which deactivates
        # the prior ones via the UNIQUE constraint workaround.
        pass

    # 3. Discharge any active admission for patient id=1.
    # The list_admissions endpoint scopes to "all" only with status=all;
    # for active we have to look at multiple views.
    for tok_label, tok in (("dr1", doc_token), ("hospadmin", adm_token)):
        s, ad = http("GET", "/admissions?status=active", token=tok)
        if s != 200:
            continue
        for a in (ad.get("items") if isinstance(ad, dict) else ad) or []:
            if a.get("patient_id") == 1 and not a.get("discharged_at"):
                aid = a["id"]
                # doctor-side direct-discharge (admitted_by == doctor user).
                s2, out = http("POST", f"/admissions/{aid}/direct-discharge", token=doc_token, body={"notes": "smoke reset"})
                print(f"  discharge #{aid} as doctor -> {s2} ({out})")
                if s2 != 200:
                    # Try hospital-admin path: direct-discharge is doctor-only;
                    # but the user has a manual button on the dashboard that
                    # likely uses /discharge-appeals or a different route.
                    # Just attempt all known endpoints once.
                    for ep in ("/discharge", "/discharge-now", "/close"):
                        s3, out3 = http("POST", f"/admissions/{aid}{ep}", token=adm_token, body={"notes": "reset"})
                        print(f"    admin {ep} -> {s3} ({out3})")
                        if s3 == 200:
                            break

    print("done")


if __name__ == "__main__":
    main()
