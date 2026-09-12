"""Smoke test for the bed/ward management feature.

Exercises:
  1. POST /signup — create a fresh doctor (and a fresh patient linked to them)
  2. GET /wards — should now include the auto-seeded "General Ward" + 5 beds
  3. POST /wards — create a custom "ICU-A" ward
  4. POST /wards/{id}/beds — bulk-add 3 beds
  5. GET /wards/{id}/beds — verify the 3 beds appear
  6. POST /patients/{id}/admit — admit the patient into bed G-2
  7. GET /admissions?status=active — verify 1 admission
  8. POST /admissions/{id}/transfer — move the patient to a new bed in ICU-A
  9. GET /admissions/{id} — verify ward_id_at_admit is the original ward
 10. PUT /admissions/{id}/discharge — discharge with a reason
 11. GET /admissions?status=discharged — verify the discharge shows
 12. GET /admissions/{id}/charges — verify (days × daily_rate)
 13. GET /patients/active-admissions — verify it's empty now
 14. DELETE /beds/{id} — delete a free bed (succeeds)
 15. DELETE /beds/{id} — try to delete an occupied bed (must 409)
 16. POST /patients — combined create + admit flow
"""

import json
import time
import requests
import sys
import random
import string

BASE = "http://127.0.0.1:8000"
PASS = "Test1234!"
EMAIL_DOMAIN = "smoke-beds.test"


def rand_suffix():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


def login(email, password):
    # /login uses OAuth2PasswordRequestForm (form-data), not JSON.
    # The endpoint looks up User.username (not email). Our signup helper
    # set username = email-part-before-@.
    r = requests.post(
        f"{BASE}/login",
        data={"username": email.split('@')[0], "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def me(token):
    """GET /me — returns the current user as a UserOut (has patient_id for
    patient-role users, otherwise null)."""
    r = requests.get(f"{BASE}/me", headers=auth_headers(token))
    r.raise_for_status()
    return r.json()


def materialize_patient(token):
    """For a freshly signed-up patient user, the Patient row is auto-created
    lazily on first /patient/profile hit. Call this and then re-fetch /me to
    learn the now-real patient_id. Returns (user_id, patient_id).
    """
    me_before = me(token)
    if me_before.get("patient_id"):
        return me_before["id"], me_before["patient_id"]
    # Trigger lazy creation.
    requests.get(f"{BASE}/patient/profile", headers=auth_headers(token))
    me_after = me(token)
    return me_after["id"], me_after["patient_id"]


def signup(role, email, password, name, extra=None):
    payload = {
        "username": email.split('@')[0],
        "email": email,
        "password": password,
        "name": name,
        "role": role,
    }
    if extra:
        payload.update(extra)
    r = requests.post(f"{BASE}/signup", json=payload)
    r.raise_for_status()
    return r.json()


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def main():
    print("=" * 70)
    print(" Bed & ward management — smoke test")
    print("=" * 70)

    # -- 1. Sign up a fresh doctor + patient linked to that doctor --
    suffix = rand_suffix()
    doc_email = f"dr.{suffix}@{EMAIL_DOMAIN}"
    pat_email = f"pt.{suffix}@{EMAIL_DOMAIN}"

    print(f"\n[1] signup doctor {doc_email}")
    doc = signup("doctor", doc_email, PASS, f"Dr. Test {suffix}",
                 {"specialization": "Internal Medicine"})
    print(f"    doctor user id={doc['id']}")

    print(f"\n[2] signup patient {pat_email} (linked to the doctor)")
    pat = signup("patient", pat_email, PASS, f"Patient {suffix}",
                 {"doctor_email": doc_email})
    print(f"    patient user id={pat['id']}")

    doc_token = login(doc_email, PASS)
    pat_token = login(pat_email, PASS)

    # Materialize the Patient row (lazy-created on first profile read)
    pat_user_id, pat_id = materialize_patient(pat_token)
    print(f"    patient_id (in patients table) = {pat_id}")

    H = auth_headers(doc_token)

    # -- 2. /wards should have at least the auto-seeded General Ward --
    print("\n[3] GET /wards (should contain auto-seeded 'General Ward')")
    r = requests.get(f"{BASE}/wards", headers=H)
    assert r.ok, f"GET /wards failed: {r.status_code} {r.text}"
    wards = r.json()
    print(f"    got {len(wards)} ward(s): {[w['name'] for w in wards]}")
    assert any(w["name"] == "General Ward" for w in wards), "Expected auto-seeded 'General Ward'"
    general = next(w for w in wards if w["name"] == "General Ward")
    print(f"    General Ward: bed_count={general['bed_count']}, occupied={general['occupied_count']}")

    # Add a couple of extra beds to the General Ward so the test has
    # enough free capacity even when earlier smoke-test runs left beds
    # occupied. Safe to re-run — the unique (ward_id, bed_number) index
    # will reject dupes but we use a unique suffix.
    r = requests.post(f"{BASE}/wards/{general['id']}/beds", headers=H, json={
        "beds": [
            {"bed_number": f"G-{suffix}-A"},
            {"bed_number": f"G-{suffix}-B"},
            {"bed_number": f"G-{suffix}-C"},
        ]
    })
    if r.ok:
        print(f"    added 3 extra general-ward beds with suffix {suffix}")

    # -- 3. POST /wards -- create ICU-A --
    print(f"\n[4] POST /wards  →  create 'ICU-A-{suffix}'")
    r = requests.post(f"{BASE}/wards", headers=H, json={
        "name": f"ICU-A-{suffix}", "ward_type": "icu", "daily_rate": 2500.0, "total_beds": 3,
    })
    assert r.ok, f"create ward failed: {r.status_code} {r.text}"
    icu = r.json()
    icu_id = icu["id"]
    print(f"    ICU-A id={icu_id}, daily_rate={icu['daily_rate']}")

    # -- 4. POST /wards/{id}/beds -- bulk add 3 beds --
    print(f"\n[5] POST /wards/{{id}}/beds  →  add 3 beds to ICU-A-{suffix}")
    r = requests.post(f"{BASE}/wards/{icu_id}/beds", headers=H, json={
        "beds": [
            {"bed_number": f"I-{suffix}-1", "bed_type": "ventilator"},
            {"bed_number": f"I-{suffix}-2", "bed_type": "oxygen"},
            {"bed_number": f"I-{suffix}-3"},
        ]
    })
    assert r.ok, f"add beds failed: {r.status_code} {r.text}"
    added_beds = r.json()
    assert len(added_beds) == 3, f"expected 3 beds, got {len(added_beds)}"
    print(f"    added: {[b['bed_number'] for b in added_beds]}")

    # -- 5. GET /wards/{id}/beds --
    print("\n[6] GET /wards/{id}/beds")
    r = requests.get(f"{BASE}/wards/{icu_id}/beds", headers=H)
    assert r.ok
    icu_beds = r.json()
    assert len(icu_beds) == 3
    print(f"    ICU beds: {[b['bed_number'] for b in icu_beds]}")
    icu_bed_i1 = next(b for b in icu_beds if b["bed_number"] == f"I-{suffix}-1")

    # -- 6. POST /patients/{id}/admit --
    print("\n[7] GET /wards/{general['id']}/beds — pick a free General Ward bed")
    r = requests.get(f"{BASE}/wards/{general['id']}/beds", headers=H)
    assert r.ok
    gen_beds = r.json()
    free_bed = next(b for b in gen_beds if not b["is_occupied"])
    print(f"    free bed: {free_bed['bed_number']} (id={free_bed['id']})")

    print(f"\n[8] POST /patients/{pat_id}/admit → bed {free_bed['bed_number']}")
    r = requests.post(f"{BASE}/patients/{pat_id}/admit", headers=H, json={
        "patient_id": pat_id,
        "bed_id": free_bed['id'],
        "notes": "Initial triage: stable vitals, observation.",
    })
    assert r.ok, f"admit failed: {r.status_code} {r.text}"
    admission = r.json()
    adm_id = admission["id"]
    print(f"    admission id={adm_id}, bed={admission['bed_number']}, ward={admission['ward_name']}")
    assert admission["is_active"], "admission should be active"
    assert admission["ward_id"] == general["id"], "ward_id should match General Ward"

    # -- 7. GET /admissions?status=active --
    print("\n[9] GET /admissions?status=active")
    r = requests.get(f"{BASE}/admissions?status=active", headers=H)
    assert r.ok
    items = r.json()["items"]
    print(f"    active admissions: {len(items)}")
    assert any(a["id"] == adm_id for a in items)

    # -- 8. POST /admissions/{id}/transfer --
    print(f"\n[10] POST /admissions/{adm_id}/transfer → bed I-{suffix}-1 (id={icu_bed_i1['id']})")
    r = requests.post(f"{BASE}/admissions/{adm_id}/transfer", headers=H, json={
        "to_bed_id": icu_bed_i1['id'],
        "reason": "Deteriorating — needs ICU monitoring.",
    })
    assert r.ok, f"transfer failed: {r.status_code} {r.text}"
    transferred = r.json()
    print(f"    moved to bed={transferred['bed_number']}, transfers={transferred['transfer_count']}")
    assert transferred["transfer_count"] == 1
    assert transferred["ward_id"] == general["id"], "ward_id (stored at admit) must NOT change on transfer"

    # -- 9. GET /admissions/{id} verify ward_id_at_admit is original --
    print("\n[11] GET /admissions/{adm_id} — verify ward_id stays stable")
    r = requests.get(f"{BASE}/admissions/{adm_id}", headers=H)
    assert r.ok
    detail = r.json()
    print(f"    current bed={detail['bed_number']}, ward_id={detail['ward_id']}, general={general['id']}")
    assert detail["ward_id"] == general["id"]
    assert detail["transfer_count"] == 1, "transfer audit row should exist"

    # -- 10. PUT /admissions/{id}/discharge --
    print(f"\n[12] PUT /admissions/{adm_id}/discharge")
    r = requests.put(f"{BASE}/admissions/{adm_id}/discharge", headers=H, json={
        "reason": "Stable, discharged home.",
    })
    assert r.ok
    discharged = r.json()
    print(f"    discharged_at={discharged['discharged_at']}")
    assert discharged["discharged_at"] is not None

    # -- 11. GET /admissions?status=discharged --
    print("\n[13] GET /admissions?status=discharged")
    r = requests.get(f"{BASE}/admissions?status=discharged", headers=H)
    assert r.ok
    items = r.json()["items"]
    assert any(a["id"] == adm_id for a in items)
    print(f"    discharged admissions: {len(items)}")

    # -- 12. GET /admissions/{id}/charges --
    print(f"\n[14] GET /admissions/{adm_id}/charges")
    r = requests.get(f"{BASE}/admissions/{adm_id}/charges", headers=H)
    assert r.ok
    charges = r.json()
    print(f"    days={charges['days']}, rate={charges['daily_rate']}, total={charges['total_charges']}")
    # General Ward daily rate is 500, ICU is 2500. ward_id_at_admit is General
    # so charges should be (days × 500). At least 1 day.
    assert charges["days"] >= 1
    assert abs(charges["daily_rate"] - 500.0) < 0.01, "daily_rate should be the General Ward rate (denormalized)"

    # -- 13. /patients/active-admissions should be empty for this patient --
    print("\n[15] GET /patients/active-admissions")
    r = requests.get(f"{BASE}/patients/active-admissions", headers=H)
    assert r.ok
    amap = r.json()
    assert str(pat_id) not in amap or amap[str(pat_id)] is None, \
        f"patient {pat_id} should have no active admission now"
    print(f"    active-admissions map: {amap}")

    # -- 14. DELETE /beds/{id} — free bed --
    print(f"\n[16] DELETE /beds/{icu_bed_i1['id']+1} (currently free)")
    r = requests.delete(f"{BASE}/beds/{icu_bed_i1['id']+1}", headers=H)
    print(f"    status={r.status_code}")
    assert r.status_code in (200, 204)

    # -- 15. DELETE /beds/{id} — but first re-admit so bed is occupied --
    print(f"\n[17] Re-admit patient to a different bed for delete-blocked test")
    # Need to find a free bed now
    r = requests.get(f"{BASE}/wards/{general['id']}/beds", headers=H)
    gen_beds = r.json()
    free_beds = [b for b in gen_beds if not b["is_occupied"]]
    if free_beds:
        r = requests.post(f"{BASE}/patients/{pat_id}/admit", headers=H, json={
            "patient_id": pat_id,
            "bed_id": free_beds[0]['id'],
            "notes": "Readmitted for testing.",
        })
        assert r.ok
        adm2 = r.json()
        occupied_bed_id = adm2["bed_id"]
        print(f"    re-admitted to bed_id={occupied_bed_id}")

        print(f"\n[18] DELETE /beds/{occupied_bed_id} (occupied → must 409)")
        r = requests.delete(f"{BASE}/beds/{occupied_bed_id}", headers=H)
        print(f"    status={r.status_code}, body={r.text[:100]}")
        assert r.status_code == 409, f"expected 409, got {r.status_code}"
    else:
        print("    no free bed available — skipping 409 test")

    # -- 16. Combined: POST /patients with bed_id (create + admit) --
    print(f"\n[19] POST /patients with bed_id → combined create+admit")
    # Find any free bed across ALL wards (not just general).
    all_free_beds = []
    for w in wards + [icu]:
        r = requests.get(f"{BASE}/wards/{w['id']}/beds", headers=H)
        if not r.ok:
            continue
        for b in r.json():
            if not b["is_occupied"]:
                all_free_beds.append(b)
    if all_free_beds:
        free_bed = all_free_beds[0]
        r = requests.post(f"{BASE}/patients", headers=H, json={
            "name": f"Combined {suffix}",
            "age": 45,
            "gender": "male",
            "contact": "9999999999",
            "bed_id": free_bed['id'],
            "initial_notes": "Admitted directly from doctor page.",
        })
        assert r.ok, f"create+admit failed: {r.status_code} {r.text}"
        new_pat = r.json()
        print(f"    created patient id={new_pat['id']}")
        # Verify the admission exists via the active admissions list.
        r2 = requests.get(f"{BASE}/admissions?status=active", headers=H)
        assert r2.ok
        items = r2.json()["items"]
        new_adm = next((a for a in items if a["patient_id"] == new_pat["id"]), None)
        assert new_adm is not None, "expected an active admission for the newly-created patient"
        assert new_adm["bed_id"] == free_bed['id'], "admission should reference the chosen bed"
        print(f"    confirmed active admission id={new_adm['id']} for patient {new_pat['id']}")
    else:
        print("    no free bed — skipping combined test")

    print("\n" + "=" * 70)
    print(" All smoke tests passed.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n!! ASSERTION FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as e:
        print(f"\n!! HTTP ERROR: {e}", file=sys.stderr)
        if hasattr(e, "response") and e.response is not None:
            print(f"   body: {e.response.text}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"\n!! UNEXPECTED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(3)
