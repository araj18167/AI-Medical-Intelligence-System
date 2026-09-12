"""End-to-end API tests for Patient Manage -> Doctors/Hospitals:
directories, doctor-link request/approval workflow, hospital linking,
care-network history, and authorization."""
import requests
import sqlite3

BASE = "http://127.0.0.1:8000"
passed = 0
failed = 0

def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}  {extra}")

def login(username, password):
    r = requests.post(BASE + "/login", data={"username": username, "password": password})
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    return r.json()["access_token"]

def auth(token):
    return {"Authorization": f"Bearer {token}"}

PAT = login("test.patient.1", "P@ss1234")       # patient user id 3, patient_id 1
DR1 = login("dr1", "Test@1234")                 # doctor user id 2
DR2 = login("doctor1111@gmail.com", "P@ss1234") # doctor user id 5
Hp = auth(PAT)
H1 = auth(DR1)
H2 = auth(DR2)

# ---- 1. Directories ----
print("\n[1] Directories")
r = requests.get(BASE + "/hospitals", headers=Hp, params={"limit": 3})
d = r.json()
check("GET /hospitals list", r.status_code == 200 and len(d.get("items", [])) >= 1)
if d["items"]:
    check("hospital item enriched", "doctor_count" in d["items"][0] and "departments" in d["items"][0])
r = requests.get(BASE + "/doctors/directory", headers=Hp, params={"limit": 5})
docs = r.json()
check("GET /doctors/directory page", r.status_code == 200 and isinstance(docs, list))
if docs:
    check("directory item has is_linked", "is_linked" in docs[0])
r = requests.get(BASE + "/doctors/5/public-profile", headers=Hp)
check("public-profile fields", r.status_code == 200 and "qualifications" in r.json())
r = requests.get(BASE + "/doctors/999999/public-profile", headers=Hp)
check("public-profile 404", r.status_code == 404)

# ---- 2. Care history (synthetic backfill from existing state) ----
print("\n[2] Care history")
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "doctor"})
ev = r.json()
check("doctor history returns list", r.status_code == 200 and isinstance(ev, list))
kinds = [e["kind"] for e in ev]
# Baseline history must exist: either synthesized from the pre-audit active
# dr1 link ("doctor_link") or real recorded events from earlier runs.
expected_kinds = {"doctor_link", "doctor_request", "doctor_unlink",
                  "doctor_request_accepted", "doctor_request_rejected", "doctor_request_cancelled"}
check("doctor history non-empty with known kinds", bool(kinds) and expected_kinds.intersection(kinds), str(kinds))
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "hospital"})
hev = r.json()
check("hospital history returns list", r.status_code == 200 and isinstance(hev, list))

# ---- 3. Doctor request -> accept workflow ----
print("\n[3] Doctor link request workflow")
# Ensure dr1 is unlinked first (restore afterwards).
r = requests.get(BASE + "/patients/mine/linked-doctors", headers=Hp)
linked_ids = [x["user_id"] for x in r.json()]
if 2 in linked_ids:
    r = requests.delete(BASE + "/patients/mine/linked-doctors/2", headers=Hp)
    check("unlink dr1 baseline", r.status_code == 204)
else:
    check("unlink dr1 baseline", True, "dr1 not linked initially")

r = requests.post(BASE + "/patients/mine/linked-doctors", headers=Hp, json={"doctor_user_id": 2})
check("POST creates request (not link)", r.status_code == 200 and r.json().get("status") == "requested", r.text[:120])
r = requests.post(BASE + "/patients/mine/linked-doctors", headers=Hp, json={"doctor_user_id": 2})
check("duplicate request -> 409", r.status_code == 409)
r = requests.get(BASE + "/patients/mine/doctor-requests", headers=Hp)
reqs = r.json()
check("patient sees pending request", r.status_code == 200 and len(reqs) == 1 and reqs[0]["doctor_name"])
r = requests.get(BASE + "/patients/mine/linked-doctors", headers=Hp)
check("not linked yet (pending)", 2 not in [x["user_id"] for x in r.json()])

r = requests.get(BASE + "/doctors/mine/link-requests", headers=H1)
incoming = r.json()
check("doctor sees pending request", r.status_code == 200 and len(incoming) == 1 and incoming[0]["patient_name"] == "Test Patient")
req_id = incoming[0]["request_id"]

r = requests.post(BASE + f"/doctors/mine/link-requests/{req_id}/accept", headers=H2)
check("other doctor cannot accept -> 403", r.status_code == 403)
r = requests.post(BASE + f"/doctors/mine/link-requests/{req_id}/accept", headers=H1)
check("doctor accepts -> active", r.status_code == 200 and r.json().get("status") == "accepted")
r = requests.get(BASE + "/patients/mine/linked-doctors", headers=Hp)
check("patient now linked to dr1", 2 in [x["user_id"] for x in r.json()])
r = requests.get(BASE + "/doctors/mine/link-requests", headers=H1)
check("doctor inbox empty after accept", r.json() == [])
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "doctor"})
kinds = [e["kind"] for e in r.json()]
check("events include request + accepted",
      "doctor_request" in kinds and "doctor_request_accepted" in kinds and "doctor_unlink" in kinds, str(kinds))

# Notifications written for both sides
con = sqlite3.connect("medical_system.db")
n1 = con.execute("SELECT COUNT(*) FROM notification_events WHERE kind='doctor_link_request' AND recipient_user_id=2").fetchone()[0]
n2 = con.execute("SELECT COUNT(*) FROM notification_events WHERE kind='doctor_link_accepted' AND recipient_user_id=3").fetchone()[0]
con.close()
check("doctor got link-request notification", n1 >= 1, str(n1))
check("patient got accepted notification", n2 >= 1, str(n2))

# ---- 4. Doctor request -> reject workflow ----
print("\n[4] Doctor link request reject")
r = requests.delete(BASE + "/patients/mine/linked-doctors/2", headers=Hp)
check("unlink dr1 for reject test", r.status_code == 204)
r = requests.post(BASE + "/patients/mine/linked-doctors", headers=Hp, json={"doctor_user_id": 2})
check("second request created", r.status_code == 200 and r.json().get("status") == "requested")
req2 = r.json()["request_id"]
r = requests.post(BASE + f"/doctors/mine/link-requests/{req2}/reject", headers=H1)
check("doctor rejects", r.status_code == 200 and r.json().get("status") == "rejected")
r = requests.get(BASE + "/patients/mine/doctor-requests", headers=Hp)
check("inbox empty after reject", r.json() == [])
r = requests.get(BASE + "/patients/mine/linked-doctors", headers=Hp)
check("not linked after reject", 2 not in [x["user_id"] for x in r.json()])
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "doctor"})
check("events include rejected", any(e["kind"] == "doctor_request_rejected" for e in r.json()))

# Patient cancel path
r = requests.post(BASE + "/patients/mine/linked-doctors", headers=Hp, json={"doctor_user_id": 2})
req3 = r.json()["request_id"]
r = requests.delete(BASE + "/patients/mine/linked-doctors/2", headers=Hp)
check("unlink when pending only -> 404", r.status_code == 404)
r = requests.delete(BASE + "/patients/mine/doctor-requests/2", headers=Hp)
check("patient cancels request", r.status_code == 204)
r = requests.get(BASE + "/patients/mine/doctor-requests", headers=Hp)
check("empty after cancel", r.json() == [])
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "doctor"})
check("events include cancelled", any(e["kind"] == "doctor_request_cancelled" for e in r.json()))

# Restore baseline: link dr1 back via request + accept.
r = requests.post(BASE + "/patients/mine/linked-doctors", headers=Hp, json={"doctor_user_id": 2})
req4 = r.json()["request_id"]
r = requests.post(BASE + f"/doctors/mine/link-requests/{req4}/accept", headers=H1)
check("re-link dr1 baseline restored", r.status_code == 200)

# ---- 5. Hospital link/unlink history ----
print("\n[5] Hospital history")
r = requests.post(BASE + "/patients/mine/linked-hospitals", headers=Hp, json={"hospital_id": 3})
check("link hospital", r.status_code == 200)
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "hospital"})
check("hospital_link event recorded", any(e["kind"] == "hospital_link" and e["hospital_id"] == 3 for e in r.json()))
r = requests.delete(BASE + "/patients/mine/linked-hospitals/3", headers=Hp)
check("unlink hospital", r.status_code == 204)
r = requests.get(BASE + "/patients/mine/care-events", headers=Hp, params={"kind": "hospital"})
check("hospital_unlink event recorded", any(e["kind"] == "hospital_unlink" and e["hospital_id"] == 3 for e in r.json()))

# ---- 6. Authorization ----
print("\n[6] Authorization")
r = requests.get(BASE + "/patients/mine/linked-hospitals", headers=H1)
check("doctor cannot read patient hospitals -> 403", r.status_code == 403)
r = requests.get(BASE + "/doctors/mine/link-requests", headers=Hp)
check("patient cannot read doctor inbox -> 403", r.status_code == 403)
r = requests.get(BASE + "/patients/mine/doctor-requests", headers=H1)
check("doctor cannot read patient requests -> 403", r.status_code == 403)
r = requests.get(BASE + "/patients/mine/care-events")
check("no token -> 401", r.status_code == 401)

print(f"\n===== {passed} passed, {failed} failed =====")
raise SystemExit(1 if failed else 0)
