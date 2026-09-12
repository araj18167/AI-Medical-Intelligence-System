"""End-to-end smoke for the new features.

Uses the seeded users dr1 / hospadmin (Test1234!) and creates a second nurse
via /signup for the transfer test. Skips creating a hospital — uses the
existing one bound to the admin.

5 sections:
  A. Doctor cancels their own pending admit request
  B. Place admit, assign nurse1, transfer to nurse2
  C. Send patient back to hospital, re-allot nurse1
  D. Hospital admin re-assigns via POST /admissions/{id}/nurse (replaces active)
  E. Nurse1 sends a report, /nurse-notes/{id}/summarize regenerates the AI summary
"""

import json
import sys
import time
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
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return e.code, payload.decode("utf-8", "replace")


def signup(username, password, role, full_name):
    body = {"username": username, "email": f"{username}@example.com",
            "password": password, "role": role, "full_name": full_name}
    s, _ = http("POST", "/signup", body=body)
    return s


def must(cond, msg):
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        sys.exit(1)


def section(t):
    print(f"\n=== {t} ===")


def main():
    ts = int(time.time())
    pw = "Test1234!"
    doctor_tok = login("dr1", pw)
    hospadmin_tok = login("hospadmin", pw)

    # Doctor's own patient — same pattern as smoke_nurse_place.py.
    s, patients = http("GET", "/patients/mine", token=doctor_tok)
    must(s == 200, "GET /patients/mine")
    patient = patients[0]

    # ---- Section 0: clean slate (discharge any active admission) ----
    s, active = http("GET", "/admissions?status=active", token=doctor_tok)
    for a in active.get("items", []):
        if a["patient_id"] == patient["id"]:
            http("POST", f"/admissions/{a['id']}/direct-discharge", token=doctor_tok, body={"reason": "smoke reset"})

    # Cancel every pending request so we have a clean state for the
    # cancel/notifications test.
    s, pending = http("GET", "/hospital-admit-requests?status=pending", token=doctor_tok)
    for r in pending.get("items", []):
        if r["patient_id"] == patient["id"]:
            http("POST", f"/hospital-admit-requests/{r['id']}/cancel", token=doctor_tok, body={})

    # Make sure we have a second nurse at the same hospital. Sign up if
    # absent. (Signup is a no-op 409 if the user already exists.)
    nurse2_user = f"nurse2smoke{ts}"
    su_status = signup(nurse2_user, pw, "nurse", "Nurse Two")
    must(su_status in (200, 201, 409), f"signup nurse2 → {su_status}")
    nurse2_tok = login(nurse2_user, pw)
    must(bool(nurse2_tok), "login nurse2")

    # ---- Find nurse1 (existing) ----
    s, nurses_resp = http("GET", "/hospitals/mine/nurses", token=hospadmin_tok)
    nurses = (nurses_resp or {}).get("items", [])
    must(len(nurses) >= 1, "list nurses (got none)")
    # Pick the first nurse that ISN'T nurse2 (avoid transferring to self).
    n1 = next((n for n in nurses if n["username"] != nurse2_user), nurses[0])
    n2 = next(n for n in nurses if n["username"] == nurse2_user)
    n1_id = n1["user_id"]
    n2_id = n2["user_id"]

    # ---- Find a free bed in the admin's hospital ----
    s, wards = http("GET", "/wards", token=hospadmin_tok)
    free_bed_id = None
    ward_list = wards if isinstance(wards, list) else (wards or {}).get("items", [])
    for w in ward_list:
        if w.get("hospital_id") != 1:
            continue
        s2, beds = http("GET", f"/wards/{w['id']}/beds", token=hospadmin_tok)
        if s2 != 200:
            continue
        for b in beds:
            if not b.get("is_occupied"):
                free_bed_id = b["id"]
                break
        if free_bed_id:
            break
    must(free_bed_id is not None, "found free bed")

    # ===== Section A: doctor cancels a pending request =====
    section("A: doctor cancel")
    s, r = http("POST", "/hospital-admit-requests", token=doctor_tok,
                body={"patient_id": patient["id"], "hospital_id": 1, "reason": "smoke A"})
    must(s in (200, 201), f"file request → {s}")
    rid = r["id"]

    s, _ = http("POST", f"/hospital-admit-requests/{rid}/cancel", token=doctor_tok, body={})
    must(s == 200, f"cancel pending → {s}")
    s, after = http("GET", "/hospital-admit-requests", token=doctor_tok)
    target = next((x for x in after.get("items", []) if x["id"] == rid), None)
    must(target and target["status"] == "cancelled", "status == cancelled")

    # Notification on the hospital side
    s, hnotes = http("GET", "/notifications/recent", token=hospadmin_tok)
    must(any(i.get("kind") == "admit_cancelled" for i in hnotes.get("items", [])),
         "admit_cancelled visible to hospital admin")

    # Negative: double-cancel
    s, _ = http("POST", f"/hospital-admit-requests/{rid}/cancel", token=doctor_tok, body={})
    must(s == 409, f"double-cancel → 409 (got {s})")

    # ===== Section B: place + transfer =====
    section("B: place + transfer nurse1→nurse2")
    s, r = http("POST", "/hospital-admit-requests", token=doctor_tok,
                body={"patient_id": patient["id"], "hospital_id": 1, "reason": "smoke B"})
    must(s in (200, 201), f"file request → {s}")
    rid_b = r["id"]

    s, adm = http("POST", f"/hospital-admit-requests/{rid_b}/place", token=hospadmin_tok,
                  body={"bed_id": free_bed_id, "nurse_user_id": n1_id, "notes": "B.place"})
    must(s in (200, 201), f"place with nurse1 → {s} body={adm if s not in (200,201) else ''}")
    admission_id = adm["id"]

    s, na = http("GET", f"/admissions/{admission_id}/nurse", token=hospadmin_tok)
    must(na["nurse_user_id"] == n1_id, "active nurse == nurse1")

    # Nurse1 transfers to nurse2
    s, t = http("POST", f"/admissions/{admission_id}/transfer-nurse", token=None,  # need nurse1 token
                body={"new_nurse_user_id": n2_id, "reason": "B.transfer"})
    # The above uses doctor_tok? No, we passed token=None. Need a nurse token
    # — nurse1's password. We don't have it directly because nurse1 is the
    # seeded nurse (any of the existing ones). Easier path: log in as a
    # known nurse. If the seeded names differ, we accept the failure and
    # skip with a note.
    nurse1_user = n1["username"]
    try:
        nurse1_tok = login(nurse1_user, pw)
    except Exception as e:
        print(f"  SKIP  cannot login as nurse1 ({nurse1_user}): {e}")
        nurse1_tok = None
    s, t = http("POST", f"/admissions/{admission_id}/transfer-nurse", token=nurse1_tok,
                body={"new_nurse_user_id": n2_id, "reason": "B.transfer"})
    if s != 200:
        print(f"  FAIL  transfer → {s} body={t}")
        sys.exit(1)
    s, after = http("GET", f"/admissions/{admission_id}/nurse", token=hospadmin_tok)
    must(after["nurse_user_id"] == n2_id, "active nurse is nurse2")

    s, n1n = http("GET", "/notifications/recent", token=nurse1_tok)
    must(any(i.get("kind") == "nurse_transferred_away" for i in n1n.get("items", [])),
         "nurse_transferred_away visible to old nurse")
    s, n2n = http("GET", "/notifications/recent", token=nurse2_tok)
    must(any(i.get("kind") == "nurse_assigned" for i in n2n.get("items", [])),
         "nurse_assigned visible to new nurse")

    # ===== Section C: send back =====
    section("C: nurse2 sends patient back")
    s, sb = http("POST", f"/admissions/{admission_id}/send-back-to-hospital", token=nurse2_tok, body={})
    if s != 200:
        print(f"  FAIL  send-back → {s} body={sb}")
        sys.exit(1)
    s, after = http("GET", f"/admissions/{admission_id}/nurse", token=hospadmin_tok)
    must(s == 404, f"after send-back no active nurse → 404 (got {s})")
    s, hnotes = http("GET", "/notifications/recent", token=hospadmin_tok)
    must(any(i.get("kind") == "patient_needs_new_nurse" for i in hnotes.get("items", [])),
         "patient_needs_new_nurse visible to hospital admin")

    # ===== Section D: re-allot =====
    section("D: hospital admin re-allots nurse1")
    s, ra = http("POST", f"/admissions/{admission_id}/nurse", token=hospadmin_tok,
                 body={"nurse_user_id": n1_id, "notes": "D.reallot"})
    must(s in (200, 201), f"re-assign → {s}")
    s, after = http("GET", f"/admissions/{admission_id}/nurse", token=hospadmin_tok)
    must(after["nurse_user_id"] == n1_id, "active nurse is nurse1 again")

    # ===== Section E: nurse1 sends a report =====
    section("E: nurse1 sends report → AI summary")
    s, note = http("POST", f"/admissions/{admission_id}/nurse-notes", token=nurse1_tok,
                   body={"note": "Wound healing well. Vitals stable. Recommend discharge in 24h.",
                         "category": "observation",
                         "send_to_doctor": True})
    must(s in (200, 201), f"create report → {s} body={note if s not in (200,201) else ''}")
    note_id = note["id"]
    must(note.get("sent_to_doctor") is True, "sent_to_doctor True")
    summary0 = note.get("summary")
    print(f"  INFO  initial summary: {('present' if summary0 else 'absent (no GEMINI_API_KEY)')}")

    # Doctor's notifications surface nurse_report_sent
    s, dnotes = http("GET", "/notifications/recent", token=doctor_tok)
    must(any(i.get("kind") == "nurse_report_sent" for i in dnotes.get("items", [])),
         "nurse_report_sent visible to doctor")

    # Doctor hits regenerate
    s, reg = http("POST", f"/nurse-notes/{note_id}/summarize", token=doctor_tok)
    must(s == 200, f"regenerate summary → {s}")

    # Listing nurse-notes for the doctor should include the summary.
    s, listed = http("GET", f"/admissions/{admission_id}/nurse-notes", token=doctor_tok)
    must(s == 200, "list nurse-notes")
    target = next((n for n in listed.get("items", []) if n["id"] == note_id), None)
    must(target is not None, "report note in listing")
    # Whether the summary is populated depends on GEMINI_API_KEY; we
    # just assert the field exists.
    must("summary" in target and "summary_generated_at" in target,
         "note shape carries summary + summary_generated_at")

    print("\nALL SMOKE STEPS PASSED ✅")


if __name__ == "__main__":
    main()
