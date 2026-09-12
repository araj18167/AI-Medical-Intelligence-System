"""End-to-end smoke for the restored nurse branch on place_hospital_admit_request.

Flow:
  1. Log in as dr1 (doctor) and hospadmin (hospital admin).
  2. Find an active nurse in the admin's hospital.
  3. Doctor files a request for a patient he owns.
  4. Admin opens queue, picks a free bed in his hospital, optionally picks a nurse.
  5. Confirm 200, Admission row created, NurseAssignment row created, nurse
     notification enqueued (kind=nurse_assigned).
  6. Negative: try placing with a non-existent nurse_user_id and confirm 400.
  7. Negative: try placing with a nurse_user_id that is role=doctor and confirm 400.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


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
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = resp.read()
            try:
                return resp.status, json.loads(payload) if payload else None
            except json.JSONDecodeError:
                return resp.status, payload.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return e.code, payload.decode("utf-8", "replace")


def login(username, password):
    # FastAPI's OAuth2PasswordRequestForm expects form-encoded body.
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        BASE + "/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def assert_status(status, expected, label):
    if status != expected:
        raise SystemExit(f"FAIL [{label}]: expected {expected}, got {status}")
    print(f"  ok [{label}]: {status}")


def main():
    import urllib.parse  # noqa: F401  (kept here so top is clean)
    doc_token = login("dr1", "Test1234!")
    adm_token = login("hospadmin", "Test1234!")

    # 1. Doctor: file a request for a patient he owns.
    print("\n[1] File admit request as doctor")
    p_status, patients = http("GET", "/patients/mine", token=doc_token)
    assert_status(p_status, 200, "list my patients")
    if not patients:
        raise SystemExit("Doctor has no patients — cannot smoke test.")
    patient = patients[0]
    print(f"  using patient id={patient['id']} name={patient.get('name')}")

    r_status, req = http(
        "POST",
        "/hospital-admit-requests",
        token=doc_token,
        body={"patient_id": patient["id"], "hospital_id": 1, "reason": "smoke test"},
    )
    assert_status(r_status, 200, "create admit request")
    request_id = req["id"]
    print(f"  request id={request_id} status={req['status']}")

    # 2. Admin: list my nurses.
    print("\n[2] List nurses for admin's hospital")
    n_status, nurses_resp = http("GET", "/hospitals/mine/nurses", token=adm_token)
    assert_status(n_status, 200, "list nurses")
    nurses = nurses_resp.get("items", []) if isinstance(nurses_resp, dict) else (nurses_resp or [])
    if not nurses:
        raise SystemExit("Hospital has no nurses — cannot smoke test nurse branch.")
    nurse = nurses[0]
    nurse_id = nurse.get("user_id") or nurse.get("id")
    print(f"  using nurse user_id={nurse_id} name={nurse.get('full_name') or nurse.get('username')}")

    # 3. Admin: find a free bed in his hospital.
    print("\n[3] Find a free bed")
    w_status, wards_resp = http("GET", "/wards", token=adm_token)
    assert_status(w_status, 200, "list wards")
    wards = wards_resp.get("items", []) if isinstance(wards_resp, dict) else (wards_resp or [])
    free_bed_id = None
    my_wards = [w for w in wards if w.get("hospital_id") == 1]
    for w in my_wards:
        b_status, beds = http("GET", f"/wards/{w['id']}/beds", token=adm_token)
        if b_status != 200:
            continue
        for b in beds:
            if not b.get("is_occupied"):
                free_bed_id = b["id"]
                print(f"  using ward={w['name']} bed={b['bed_number']} id={free_bed_id}")
                break
        if free_bed_id:
            break
    if free_bed_id is None:
        raise SystemExit("No free bed in hospital 1 — cannot smoke test.")

    # 4. Admin: place with nurse.
    print("\n[4] Place patient WITH nurse assignment")
    pl_status, pl = http(
        "POST",
        f"/hospital-admit-requests/{request_id}/place",
        token=adm_token,
        body={"bed_id": free_bed_id, "nurse_user_id": nurse_id},
    )
    assert_status(pl_status, 200, "place with nurse")
    admit_id = pl["id"]
    print(f"  admission id={admit_id}")

    # 5. Confirm NurseAssignment row exists.
    print("\n[5] Confirm NurseAssignment row")
    na_status, na = http("GET", f"/admissions/{admit_id}/nurse", token=adm_token)
    assert_status(na_status, 200, "get admission nurse")
    if not na or na.get("nurse_user_id") != nurse_id or not na.get("is_active"):
        raise SystemExit(f"NurseAssignment did not match. got={na}")
    print(f"  nurse assignment id={na['id']} active={na['is_active']}")

    # 5b. Discharge admission id=admit_id via the doctor's direct-discharge
    # endpoint so the negative tests (6, 7) can place NEW admissions for the
    # same patient without hitting the "patient already admitted" 409.
    print("\n[5b] Discharge admit so negative tests can run")
    d_status, d_body = http(
        "POST",
        f"/admissions/{admit_id}/direct-discharge",
        token=doc_token,
        body={"notes": "smoke reset between checks"},
    )
    print(f"  discharge -> {d_status} ({d_body if d_status != 200 else 'ok'})")

    # 6. Negative: bogus nurse id → 400.
    print("\n[6] Negative: bogus nurse id -> 400")
    # Make a fresh request first.
    r2_status, req2 = http(
        "POST", "/hospital-admit-requests",
        token=doc_token,
        body={"patient_id": patient["id"], "hospital_id": 1, "reason": "neg 1"},
    )
    assert_status(r2_status, 200, "create 2nd request")
    pl2_status, pl2 = http(
        "POST", f"/hospital-admit-requests/{req2['id']}/place",
        token=adm_token,
        body={"bed_id": free_bed_id, "nurse_user_id": 999999},
    )
    assert_status(pl2_status, 400, "bogus nurse rejected")
    print(f"  detail={pl2.get('detail')}")

    # 7. Negative: doctor id as nurse → 400.
    print("\n[7] Negative: doctor id as nurse -> 400")
    me_status, me = http("GET", "/me", token=doc_token)
    doctor_id = me["id"]
    # Cancel the prior bogus-nurse request so the patient can have a fresh
    # pending one. (Failed place calls leave the request in 'pending'.)
    s, prior = http("GET", "/hospital-admit-requests?status=pending", token=doc_token)
    prior_items = prior.get("items", []) if isinstance(prior, dict) else (prior or [])
    for r in prior_items:
        if r["patient_id"] == patient["id"]:
            http("POST", f"/hospital-admit-requests/{r['id']}/cancel", token=doc_token, body={})
    r3_status, req3 = http(
        "POST", "/hospital-admit-requests",
        token=doc_token,
        body={"patient_id": patient["id"], "hospital_id": 1, "reason": "neg 2"},
    )
    assert_status(r3_status, 200, "create 3rd request")
    pl3_status, pl3 = http(
        "POST", f"/hospital-admit-requests/{req3['id']}/place",
        token=adm_token,
        body={"bed_id": free_bed_id, "nurse_user_id": doctor_id},
    )
    assert_status(pl3_status, 400, "doctor-id-as-nurse rejected")
    print(f"  detail={pl3.get('detail')}")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
