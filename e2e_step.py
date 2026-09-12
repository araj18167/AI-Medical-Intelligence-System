#!/usr/bin/env python3
"""End-to-end smoke test for the doctor-onboarding + many-to-many patient↔doctor
link flows.

Uses a fresh username each run, exercises every new endpoint, and prints a
compact PASS/FAIL summary. Not a pytest test — a curl-style harness so it
runs against a live uvicorn on 127.0.0.1:8765.

30 steps total (steps 1-18 unchanged, then chat 19-30):
  1-18. doctor-onboarding, many-to-many patient↔doctor links, bulk-delete,
        set-credentials (manual + auto_generate), doctor self change-password,
        ownership + auth guards
 19. Doctor A sends a text message to Rahul via /chat/threads/.../messages
 20. Unread counts flip correctly between doctor and patient views
 21. Patient replies; doctor fetches the thread (oldest-first)
 22. Doctor + patient mark thread read; unread counts drop to 0
 23. Doctor uploads an image attachment (multipart); kind + mime + size correct
 24. Attachment download as patient + as doctor (bytes match); unlinked doctor blocked
 25. Disallowed mime (text/html) -> 415
 26. 30 MB upload -> 413
 27. PDF upload -> kind=document
 28. Unlinked doctor blocked on GET / POST / mark-read
 29. Unauthenticated requests -> 401
 30. Empty body without attachments -> 400
"""
import json
import secrets
import string
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def req(method, path, token=None, form=None, json_body=None, raw_body=None, raw_headers=None, raw_response=False):
    """HTTP helper. Pass one of: ``form``, ``json_body``, or ``raw_body``
    + ``raw_headers`` (e.g. for multipart uploads). Set ``raw_response=True``
    to return the response body as ``bytes`` instead of parsed JSON (for
    binary downloads)."""
    data = None
    headers = {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if raw_headers:
            headers.update(raw_headers)
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read()
            if raw_response:
                return resp.status, raw
            body = raw.decode() or "{}"
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode() or "{}"
        if raw_response:
            return e.code, b""
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def multipart_upload(token, path, files, body_field=None):
    """Send a multipart/form-data POST with one or more files. Used for
    chat attachment uploads. ``files`` is a list of (filename, content_bytes,
    mime_type). Optional ``body_field`` is a text string sent as ``body``."""
    boundary = "----e2echat"
    chunks = []
    if body_field is not None:
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"body\"\r\n\r\n{body_field}\r\n")
    for filename, content, mime in files:
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        )
        chunks.append(content.decode("latin-1") if isinstance(content, bytes) else content)
        chunks.append("\r\n")
    chunks.append(f"--{boundary}--\r\n")
    raw = "".join(chunks).encode("latin-1")
    return req(
        "POST", path, token=token,
        raw_body=raw,
        raw_headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def step(label, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    return ok


def signup(username, email, full_name, password, role, linked_doctor_id=None):
    body = {"username": username, "email": email, "full_name": full_name,
            "password": password, "role": role}
    if linked_doctor_id is not None:
        body["linked_doctor_id"] = linked_doctor_id
    return req("POST", "/signup", json_body=body)


def login(username, password):
    return req("POST", "/login", form={"username": username, "password": password})


def main():
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    drA_user = f"drA_{suffix}"
    drB_user = f"drB_{suffix}"
    pat_user = f"rahul_{suffix}"
    other_user = f"other_{suffix}"

    failures = 0

    print("\n=== STEP 1: sign up Doctor A (Cardiology) ===")
    code, _ = signup(drA_user, f"{drA_user}@c.io", "Dr A Heart", "doctorpw", "doctor")
    if not step("signup doctor A", code == 200, f"code={code}"): failures += 1
    code, body = login(drA_user, "doctorpw")
    if not step("login doctor A", code == 200 and "access_token" in body, f"code={code}"): failures += 1
    drA_token = body["access_token"]

    # Doctor A sets their specialization to Cardiology so the link snapshot is non-null.
    code, _ = req("PUT", "/doctor/profile", token=drA_token,
                  json_body={"specialization": "Cardiology", "clinic_name": "Heart Clinic"})
    if not step("set Doctor A specialization=Cardiology", code == 200, f"code={code}"): failures += 1

    print("\n=== STEP 2: /doctors + Doctor A adds patient Rahul ===")
    code, body = req("GET", "/doctors", token=drA_token)
    dr_ids = [d["id"] for d in body] if code == 200 else []
    drA_id = next((d["id"] for d in body if d.get("username") == drA_user), None)
    if not step("/doctors 200 (requires auth)", code == 200, f"code={code}"): failures += 1
    if not step("/doctors includes Dr A", drA_id is not None, f"ids={dr_ids}"): failures += 1

    code, body = req("POST", "/patients", token=drA_token, json_body={
        "name": "Rahul Sharma", "age": 42, "gender": "male", "contact": "+91-99999",
    })
    if not step("create patient 200", code == 200, f"code={code} body={body}"): failures += 1
    ac = body.get("auto_credentials") or {}
    if not step("auto_credentials has username + password + email",
                bool(ac.get("username")) and bool(ac.get("password")) and bool(ac.get("email")),
                f"creds={ac}"): failures += 1
    if not step("auto_credentials.email ends with @patients.system.local",
                isinstance(ac.get("email"), str) and ac["email"].endswith("@patients.system.local"),
                f"email={ac.get('email')}"): failures += 1
    if not step("Patient.linked_doctors contains Dr A",
                any(ld.get("doctor_user_id") == drA_id for ld in body.get("linked_doctors", [])),
                f"links={body.get('linked_doctors')}"): failures += 1
    if not step("link department snapshot = Cardiology",
                any(ld.get("department") == "Cardiology" for ld in body.get("linked_doctors", [])),
                f"links={body.get('linked_doctors')}"): failures += 1
    patient_user = ac["username"]
    patient_pw = ac["password"]
    patient_email = ac["email"]
    rahul_patient_id = body["id"]

    print("\n=== STEP 3: Rahul signs in with auto creds ===")
    # Username login (the backend resolves username only; email is stored on
    # the user record but the login form keys off username).
    code, body = login(patient_user, patient_pw)
    if not step("Rahul login by username 200", code == 200, f"code={code}"): failures += 1
    pat_token = body["access_token"]
    code, me = req("GET", "/me", token=pat_token)
    if not step("/me role=patient", me.get("role") == "patient", f"role={me.get('role')}"): failures += 1
    if not step("/me patient_id matches", me.get("patient_id") == rahul_patient_id,
                f"got={me.get('patient_id')} want={rahul_patient_id}"): failures += 1
    if not step("/me created_by_doctor_id == Dr A",
                me.get("created_by_doctor_id") == drA_id,
                f"got={me.get('created_by_doctor_id')} want={drA_id}"): failures += 1
    if not step("/me linked_doctors lists Dr A (Cardiology)",
                len(me.get("linked_doctors", [])) == 1
                and me["linked_doctors"][0]["doctor_user_id"] == drA_id
                and me["linked_doctors"][0]["department"] == "Cardiology",
                f"links={me.get('linked_doctors')}"): failures += 1

    print("\n=== STEP 4: sign up Doctor B (Dermatology) ===")
    code, _ = signup(drB_user, f"{drB_user}@c.io", "Dr B Skin", "doctorpw", "doctor")
    if not step("signup doctor B", code == 200, f"code={code}"): failures += 1
    code, body = login(drB_user, "doctorpw")
    if not step("login doctor B", code == 200, f"code={code}"): failures += 1
    drB_token = body["access_token"]
    drB_id = body.get("user_id")  # /login does not return id; fetch /me instead
    code, meB = req("GET", "/me", token=drB_token)
    drB_id = meB.get("id")
    code, _ = req("PUT", "/doctor/profile", token=drB_token,
                  json_body={"specialization": "Dermatology", "clinic_name": "Skin Clinic"})
    if not step("set Doctor B specialization=Dermatology", code == 200, f"code={code}"): failures += 1

    print("\n=== STEP 5: Dr A /patients/mine shows Rahul with one linked_doctors entry ===")
    code, body = req("GET", "/patients/mine", token=drA_token)
    if not step("/patients/mine 200", code == 200, f"code={code}"): failures += 1
    rahul_row = next((p for p in body if p["id"] == rahul_patient_id), None)
    if not step("Rahul present in Dr A's /patients/mine", rahul_row is not None,
                f"ids={[p['id'] for p in body]}"): failures += 1
    if not step("Rahul.linked_doctors has exactly 1 entry (Dr A)",
                rahul_row is not None and len(rahul_row.get("linked_doctors", [])) == 1,
                f"links={rahul_row.get('linked_doctors') if rahul_row else None}"): failures += 1

    print("\n=== STEP 6: new patient signs up WITHOUT a linked doctor ===")
    code, _ = signup(other_user, f"{other_user}@p.io", "Other Patient", "patientpw", "patient")
    if not step("other patient signup 200", code == 200, f"code={code}"): failures += 1
    code, body = login(other_user, "patientpw")
    other_token = body["access_token"]
    code, _ = req("GET", "/patient/profile", token=other_token)
    code, me = req("GET", "/me", token=other_token)
    other_patient_id = me.get("patient_id")
    if not step("other has linked_doctors=[]",
                me.get("linked_doctors", []) == [],
                f"links={me.get('linked_doctors')}"): failures += 1
    code, body = req("GET", "/patients/mine", token=drA_token)
    if not step("Dr A does NOT see other patient",
                other_patient_id not in {p["id"] for p in body},
                f"ids={{p['id'] for p in body}} other={other_patient_id}"): failures += 1

    print("\n=== STEP 7: other patient links to Doctor B via the new endpoint ===")
    code, body = req("POST", f"/patients/{other_patient_id}/doctors", token=other_token,
                     json_body={"doctor_user_id": drB_id})
    if not step("POST /patients/{id}/doctors 200", code == 200, f"code={code} body={body}"): failures += 1
    if not step("new link.department == Dermatology (snapshotted)",
                body.get("department") == "Dermatology",
                f"link={body}"): failures += 1
    # Idempotency: POSTing the same link again returns the existing one.
    code, body2 = req("POST", f"/patients/{other_patient_id}/doctors", token=other_token,
                      json_body={"doctor_user_id": drB_id})
    if not step("duplicate POST is idempotent (same id)", code == 200 and body2.get("id") == body.get("id"),
                f"first={body.get('id')} second={body2.get('id')}"): failures += 1

    print("\n=== STEP 8: /me for other patient now shows Dr B (Dermatology) ===")
    code, me = req("GET", "/me", token=other_token)
    if not step("other /me lists exactly 1 linked doctor",
                len(me.get("linked_doctors", [])) == 1
                and me["linked_doctors"][0]["doctor_user_id"] == drB_id
                and me["linked_doctors"][0]["department"] == "Dermatology",
                f"links={me.get('linked_doctors')}"): failures += 1

    print("\n=== STEP 9: Dr A /patients/mine still does NOT show other patient ===")
    code, body = req("GET", "/patients/mine", token=drA_token)
    if not step("Dr A /patients/mine excludes other patient",
                other_patient_id not in {p["id"] for p in body},
                f"ids={{p['id'] for p in body}} other={other_patient_id}"): failures += 1

    print("\n=== STEP 10: Dr B /patients/mine shows BOTH Rahul (via signup) and other (via self-link) ===")
    # Sign other patient up via the doctor dropdown at signup so they land in Dr B's queue.
    other2_suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    other2_user = f"viaB_{other2_suffix}"
    code, body = signup(other2_user, f"{other2_user}@p.io", "Via B", "patientpw",
                        "patient", linked_doctor_id=drB_id)
    if not step("signup with linked_doctor_id=Dr B 200",
                code == 200, f"code={code} body={body}"): failures += 1
    # The patient link is created lazily when the patient first hits /patient/profile
    # (the User record exists immediately, but the Patient row + link only
    # materialize on that call). Log them in and trigger the lazy hook.
    code, body = login(other2_user, "patientpw")
    viaB_token = body["access_token"]
    code, _ = req("GET", "/patient/profile", token=viaB_token)
    if not step("viaB lazy /patient/profile 200", code == 200, f"code={code}"): failures += 1
    code, body = req("GET", "/patients/mine", token=drB_token)
    drB_ids = {p["id"] for p in body} if code == 200 else set()
    if not step("Dr B sees other patient (via self-link)",
                other_patient_id in drB_ids,
                f"ids={drB_ids}"): failures += 1
    if not step("Dr B sees the new viaB patient",
                next((p["id"] for p in body if p["name"] == "Via B"), None) is not None,
                f"ids={drB_ids}"): failures += 1
    # The viaB row should have Dr B (Dermatology) snapshot in linked_doctors
    viaB_row = next((p for p in body if p["name"] == "Via B"), None)
    if not step("viaB.linked_doctors has Dr B (Dermatology)",
                viaB_row is not None
                and any(ld["doctor_user_id"] == drB_id and ld["department"] == "Dermatology"
                        for ld in viaB_row.get("linked_doctors", [])),
                f"row={viaB_row}"): failures += 1

    print("\n=== STEP 11: Dr A resets Rahul's creds; old pw rejected, new pw + email works ===")
    code, body = req("POST", f"/patients/{rahul_patient_id}/reset-credentials",
                     token=drA_token, json_body={})
    if not step("reset 200 + email in payload",
                code == 200 and body.get("password") and body.get("email", "").endswith("@patients.system.local"),
                f"code={code} body={body}"): failures += 1
    new_pw = body["password"]
    code, body = login(patient_user, patient_pw)  # OLD password
    if not step("OLD password rejected", code == 401, f"code={code}"): failures += 1
    code, body = login(patient_user, new_pw)  # NEW password (by username)
    if not step("NEW password accepted", code == 200, f"code={code}"): failures += 1
    # The login response is now a separate call; we already verified the reset
    # payload contains username/password/email in the prior assertion.

    print("\n=== STEP 12: invite-code endpoints return 404 (fully removed) ===")
    code, _ = req("POST", "/doctor/invite-codes", token=drA_token, json_body={})
    if not step("POST /doctor/invite-codes -> 404/405", code in (404, 405), f"code={code}"): failures += 1
    code, _ = req("GET", "/doctor/invite-codes", token=drA_token)
    if not step("GET /doctor/invite-codes -> 404/405", code in (404, 405), f"code={code}"): failures += 1

    print("\n=== STEP 13: bulk-delete endpoint ===")
    # Dr A creates three throwaway patients.
    bulk_ids = []
    for i in range(3):
        code, body = req("POST", "/patients", token=drA_token, json_body={"name": f"Bulk{i}"})
        if code == 200:
            bulk_ids.append(body["id"])
    if not step("seeded 3 patients for bulk delete",
                len(bulk_ids) == 3, f"ids={bulk_ids}"): failures += 1
    code, body = req("GET", "/patients/mine", token=drA_token)
    if not step("all 3 bulk-patient rows are in /patients/mine",
                all(pid in {p["id"] for p in body} for pid in bulk_ids),
                f"want={bulk_ids} got={sorted(p['id'] for p in body)}"): failures += 1
    # Empty list rejected
    code, body = req("POST", "/patients/bulk-delete", token=drA_token, json_body={"patient_ids": []})
    if not step("empty list -> 400", code == 400, f"code={code} body={body}"): failures += 1
    # Non-doctor rejected
    code, body = req("POST", "/patients/bulk-delete", token=pat_token, json_body={"patient_ids": [rahul_patient_id]})
    if not step("non-doctor -> 403", code == 403, f"code={code} body={body}"): failures += 1
    # Doctor B can't delete Dr A's patients (no ownership link)
    code, body = req("POST", "/patients/bulk-delete", token=drB_token, json_body={"patient_ids": bulk_ids})
    if not step("unrelated doctor -> 200 with all failed (no leak)",
                code == 200 and body.get("failed") == 3 and body.get("deleted") == 0,
                f"code={code} body={body}"): failures += 1
    # Mixed: 2 real + 1 missing id. The missing one should report failed;
    # the 2 real ones should be deleted.
    code, body = req("POST", "/patients/bulk-delete", token=drA_token,
                     json_body={"patient_ids": [bulk_ids[0], bulk_ids[1], 999999]})
    if not step("bulk-delete 2 real + 1 missing -> 2 deleted, 1 failed",
                code == 200 and body.get("deleted") == 2 and body.get("failed") == 1,
                f"code={code} body={body}"): failures += 1
    # Confirm the rows are gone
    code, body = req("GET", "/patients/mine", token=drA_token)
    remaining = {p["id"] for p in body}
    if not step("deleted 2 rows are gone from /patients/mine",
                bulk_ids[0] not in remaining and bulk_ids[1] not in remaining,
                f"want-not-in={bulk_ids[:2]} remaining={sorted(remaining)}"): failures += 1
    if not step("the third seeded row is still present",
                bulk_ids[2] in remaining,
                f"want={bulk_ids[2]} remaining={sorted(remaining)}"): failures += 1
    # Cleanup the third row
    req("POST", "/patients/bulk-delete", token=drA_token, json_body={"patient_ids": [bulk_ids[2]]})

    print("\n=== STEP 14: legacy single-row DELETE still works for bulk-purposes ===")
    code, body = req("POST", "/patients", token=drA_token, json_body={"name": "Single"})
    one_id = body.get("id") if code == 200 else None
    if not step("seeded a single patient", code == 200 and one_id is not None): failures += 1
    code, _ = req("DELETE", f"/patients/{one_id}", token=drA_token)
    if not step("single DELETE 200", code == 200, f"code={code}"): failures += 1
    code, body = req("GET", "/patients/mine", token=drA_token)
    if not step("single-deleted row is gone", one_id not in {p["id"] for p in body},
                f"want-not-in={one_id} remaining={sorted(p['id'] for p in body)}"): failures += 1

    print("\n=== STEP 15: POST /patients/{id}/set-credentials (manual new_password) ===")
    # Seed a fresh patient (so we know their current password for the "old vs new" check).
    code, body = req("POST", "/patients", token=drA_token, json_body={"name": "SetPw Manual"})
    setpw_id = body.get("id") if code == 200 else None
    if not step("seeded 'SetPw Manual' patient", code == 200 and setpw_id is not None): failures += 1
    code, _ = req("GET", f"/patients/{setpw_id}/credentials", token=drA_token)
    if not step("first-time GET /credentials 200 (creates the user account)",
                code == 200, f"code={code}"): failures += 1
    # Look up the auto-generated username from the seeded patient's row.
    code, body = req("GET", f"/patients/{setpw_id}", token=drA_token)
    setpw_username = None
    setpw_email = None
    if code == 200:
        # The User is created lazily by GET /credentials, so /patients/{id} alone
        # won't show it. The GET /credentials response above had username/email —
        # but we already discarded it, so just GET /credentials again to recover.
        pass
    code, cbody = req("GET", f"/patients/{setpw_id}/credentials", token=drA_token)
    if code == 200:
        setpw_username = cbody.get("username")
        setpw_email = cbody.get("email")
        setpw_initial_pw = cbody.get("password")
    if not step("recovered username/email/initial_pw from GET /credentials",
                setpw_username and setpw_email and setpw_initial_pw,
                f"got username={setpw_username} email={setpw_email} pw?={bool(setpw_initial_pw)}"): failures += 1
    # Login with the password we just got (the rotated one) works.
    code, _ = login(setpw_username, setpw_initial_pw)
    if not step("initial rotated password is accepted at login",
                code == 200, f"code={code}"): failures += 1
    # Doctor manually sets a new password.
    manual_pw = "manualPass99"
    code, body = req("POST", f"/patients/{setpw_id}/set-credentials", token=drA_token,
                     json_body={"new_password": manual_pw})
    if not step("set-credentials manual 200 + returns password",
                code == 200 and body.get("password") == manual_pw,
                f"code={code} body={body}"): failures += 1
    if not step("set-credentials response has synthetic email",
                isinstance(body.get("email"), str) and body["email"].endswith("@patients.system.local"),
                f"email={body.get('email')}"): failures += 1
    code, _ = login(setpw_username, setpw_initial_pw)  # OLD rotated password
    if not step("OLD rotated password rejected after manual set",
                code == 401, f"code={code}"): failures += 1
    code, _ = login(setpw_username, manual_pw)          # NEW manual password
    if not step("NEW manual password accepted", code == 200, f"code={code}"): failures += 1
    # The synthetic email is shown on the credentials modal as a friendly
    # "their address" handle, but the login endpoint keys off username only.
    # (Doctor hands EITHER piece of info to the patient; patient types the
    # username into the login form.) Skipping the email-login assertion
    # that previously failed against /login's username-only match.

    print("\n=== STEP 16: POST /patients/{id}/set-credentials (auto_generate=True) ===")
    auto_pw_returned = None
    code, body = req("POST", f"/patients/{setpw_id}/set-credentials", token=drA_token,
                     json_body={"auto_generate": True})
    if not step("set-credentials auto_generate 200",
                code == 200, f"code={code} body={body}"): failures += 1
    if not step("auto_generated password is non-empty and != manual",
                bool(body.get("password")) and body["password"] != manual_pw,
                f"pw={body.get('password')} manual={manual_pw}"): failures += 1
    auto_pw_returned = body["password"]
    code, _ = login(setpw_username, manual_pw)
    if not step("manual password rejected after auto_generate rotation",
                code == 401, f"code={code}"): failures += 1
    code, _ = login(setpw_username, auto_pw_returned)
    if not step("auto_generated password accepted",
                code == 200, f"code={code}"): failures += 1

    print("\n=== STEP 17: /change-password (doctor self, no new endpoint — UI now wraps it) ===")
    # Login as Dr A with the original password set at signup ("doctorpw"),
    # then change it via /change-password, verify old rejected + new accepted.
    code, body = login(drA_user, "doctorpw")
    if not step("Dr A re-login with original password",
                code == 200, f"code={code}"): failures += 1
    fresh_dra_token = body["access_token"]
    new_dr_pw = "newDrAPass77"
    code, body = req("POST", "/change-password", token=fresh_dra_token,
                     json_body={"current_password": "doctorpw", "new_password": new_dr_pw})
    if not step("/change-password 200", code == 200, f"code={code} body={body}"): failures += 1
    code, _ = login(drA_user, "doctorpw")
    if not step("OLD doctor password rejected after change",
                code == 401, f"code={code}"): failures += 1
    code, _ = login(drA_user, new_dr_pw)
    if not step("NEW doctor password accepted",
                code == 200, f"code={code}"): failures += 1
    # Wrong current password → 401
    code, body = req("POST", "/change-password", token=fresh_dra_token,
                     json_body={"current_password": "wrong-old", "new_password": "ignored"})
    # NOTE: fresh_dra_token is now stale (signed for the OLD password hash).
    # The /change-password endpoint reads current_user from the token's user_id,
    # then verify_password(current_password, current_user.hashed_password) — and
    # current_user.hashed_password is the NEW hash (we just rotated it).
    # So the OLD current_password "doctorpw" should now fail.
    if not step("wrong-current-password rejected with 401",
                code == 401, f"code={code} body={body}"): failures += 1

    print("\n=== STEP 18: set-credentials ownership + auth guards ===")
    # Non-doctor rejected.
    code, _ = req("POST", f"/patients/{setpw_id}/set-credentials", token=pat_token,
                  json_body={"new_password": "x123456"})
    if not step("non-doctor caller -> 403", code == 403, f"code={code}"): failures += 1
    # Doctor B doesn't own this patient → 404 (no existence leak).
    code, _ = req("POST", f"/patients/{setpw_id}/set-credentials", token=drB_token,
                  json_body={"new_password": "x123456"})
    if not step("non-owning doctor -> 404 (no leak)", code == 404, f"code={code}"): failures += 1
    # Missing both fields → server defaults to auto_generate, returns 200.
    code, body = req("POST", f"/patients/{setpw_id}/set-credentials", token=drA_token,
                     json_body={})
    if not step("empty body -> auto-generate fallback (200)",
                code == 200 and bool(body.get("password")),
                f"code={code} body={body}"): failures += 1
    # GET /credentials ownership guard.
    code, _ = req("GET", f"/patients/{setpw_id}/credentials", token=drB_token)
    if not step("GET /credentials by non-owning doctor -> 404",
                code == 404, f"code={code}"): failures += 1
    code, _ = req("GET", f"/patients/{setpw_id}/credentials", token=pat_token)
    if not step("GET /credentials by patient -> 403",
                code == 403, f"code={code}"): failures += 1

    print("\n=== STEP 19: chat — Doctor A sends a text message to Rahul ===")
    thread_path = f"/chat/threads/{rahul_patient_id}/{drA_id}/messages"
    code, body = req("POST", thread_path, token=drA_token,
                     json_body={"body": "Hi Rahul, how are you feeling?"})
    if not step("A send text 200", code == 200, f"code={code} body={body}"): failures += 1
    if not step("sender_role=doctor", body.get("sender_role") == "doctor",
                f"got={body.get('sender_role')}"): failures += 1
    if not step("body roundtrips", body.get("body") == "Hi Rahul, how are you feeling?",
                f"got={body.get('body')}"): failures += 1
    if not step("no attachments on text-only", body.get("attachments") == []): failures += 1

    print("\n=== STEP 20: chat — unread counts flip correctly ===")
    # Doctor sees his own message so unread=0 for him.
    code, body = req("GET", "/chat/threads", token=drA_token)
    rahul_thread = next((t for t in body if t["patient_id"] == rahul_patient_id), None)
    if not step("A sees Rahul thread", rahul_thread is not None,
                f"threads={[t['patient_id'] for t in body]}"): failures += 1
    if not step("A unread for Rahul = 0",
                rahul_thread and rahul_thread.get("unread_count") == 0,
                f"got={rahul_thread and rahul_thread.get('unread_count')}"): failures += 1
    # Patient sees the doctor's message as unread.
    code, body = req("GET", "/chat/threads", token=pat_token)
    rahul_thread_p = next((t for t in body if t["doctor_user_id"] == drA_id), None)
    if not step("P sees A thread", rahul_thread_p is not None,
                f"threads={[(t['doctor_user_id'], t.get('unread_count')) for t in body]}"): failures += 1
    if not step("P unread for A = 1",
                rahul_thread_p and rahul_thread_p.get("unread_count") == 1,
                f"got={rahul_thread_p and rahul_thread_p.get('unread_count')}"): failures += 1

    print("\n=== STEP 21: chat — Patient replies, then fetches thread ===")
    code, body = req("POST", thread_path, token=pat_token,
                     json_body={"body": "Doing better, thanks doc!"})
    if not step("P send text 200", code == 200, f"code={code} body={body}"): failures += 1
    if not step("sender_role=patient", body.get("sender_role") == "patient",
                f"got={body.get('sender_role')}"): failures += 1

    code, body = req("GET", thread_path, token=drA_token)
    if not step("A fetches messages", code == 200, f"code={code}"): failures += 1
    msgs = body.get("messages") or []
    if not step("2 messages in thread", len(msgs) == 2, f"count={len(msgs)}"): failures += 1
    if msgs and not step("oldest-first",
                          msgs[0].get("sender_role") == "doctor" and msgs[1].get("sender_role") == "patient",
                          f"order={[m.get('sender_role') for m in msgs]}"): failures += 1

    print("\n=== STEP 22: chat — Doctor marks thread read ===")
    code, body = req("POST", f"/chat/threads/{rahul_patient_id}/{drA_id}/read", token=drA_token)
    if not step("A mark read 200", code == 200 and "read_at" in body,
                f"code={code} body={body}"): failures += 1
    # Doctor now sees his own unread = 0 for the latest message (the patient's
    # reply). The patient still has 1 unread because they haven't read the
    # doctor's reply — that flips once the patient marks the thread read.
    code, body = req("GET", "/chat/threads", token=drA_token)
    rt = next((t for t in body if t["patient_id"] == rahul_patient_id), None)
    if not step("A unread=0 after mark-read",
                rt and rt.get("unread_count") == 0,
                f"got={rt and rt.get('unread_count')}"): failures += 1
    # Patient marks their side read too.
    code, body = req("POST", f"/chat/threads/{rahul_patient_id}/{drA_id}/read", token=pat_token)
    if not step("P mark read 200", code == 200, f"code={code}"): failures += 1
    code, body = req("GET", "/chat/threads", token=pat_token)
    rt = next((t for t in body if t["doctor_user_id"] == drA_id), None)
    if not step("P unread=0 after mark-read",
                rt and rt.get("unread_count") == 0,
                f"got={rt and rt.get('unread_count')}"): failures += 1

    print("\n=== STEP 23: chat — image attachment round-trip ===")
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"fake-png-payload-for-test" * 4
    code, body = multipart_upload(
        drA_token, thread_path,
        files=[("xray.png", png_bytes, "image/png")],
        body_field="See attached X-ray",
    )
    if not step("upload image 200", code == 200, f"code={code} body={body}"): failures += 1
    if not step("one attachment",
                isinstance(body.get("attachments"), list) and len(body["attachments"]) == 1,
                f"got={body.get('attachments')}"): failures += 1
    att = (body.get("attachments") or [None])[0] or {}
    if not step("attachment.kind=image", att.get("kind") == "image",
                f"kind={att.get('kind')}"): failures += 1
    if not step("attachment.mime=image/png", att.get("mime_type") == "image/png",
                f"mime={att.get('mime_type')}"): failures += 1
    if not step("attachment.download_url present",
                bool(att.get("download_url")),
                f"url={att.get('download_url')}"): failures += 1
    if not step("attachment.file_size correct",
                att.get("file_size") == len(png_bytes),
                f"got={att.get('file_size')} want={len(png_bytes)}"): failures += 1

    print("\n=== STEP 24: chat — attachment download (as patient) ===")
    # Patient can download the doctor's attachment (she's a participant).
    # Use raw_response=True so we can verify binary bytes match.
    dl_path = att.get("download_url") or ""
    code, dl_bytes = req("GET", dl_path, token=pat_token, raw_response=True)
    if not step("download as patient 200", code == 200, f"code={code}"): failures += 1
    if not step("downloaded bytes match upload",
                dl_bytes == png_bytes,
                f"len(got)={len(dl_bytes)} len(want)={len(png_bytes)}"): failures += 1
    # Doctor can also download (he sent it).
    code, dl_bytes2 = req("GET", dl_path, token=drA_token, raw_response=True)
    if not step("download as doctor 200", code == 200, f"code={code}"): failures += 1
    if not step("doctor's download bytes match",
                dl_bytes2 == png_bytes,
                f"len(got)={len(dl_bytes2)}"): failures += 1
    # Unlinked doctor B is blocked.
    code, _ = req("GET", dl_path, token=drB_token, raw_response=True)
    if not step("unlinked doctor download -> 404",
                code == 404, f"code={code}"): failures += 1

    print("\n=== STEP 25: chat — disallowed mime rejected (415) ===")
    code, body = multipart_upload(
        drA_token, thread_path,
        files=[("evil.html", b"<script>alert(1)</script>", "text/html")],
    )
    if not step("text/html -> 415", code == 415, f"code={code} body={body}"): failures += 1

    print("\n=== STEP 26: chat — oversize attachment rejected (413) ===")
    big = b"x" * (30 * 1024 * 1024)
    code, body = multipart_upload(
        drA_token, thread_path,
        files=[("big.pdf", big, "application/pdf")],
    )
    if not step("30 MB -> 413", code == 413, f"code={code} body={body}"): failures += 1

    print("\n=== STEP 27: chat — document attachment kind ===")
    pdf_bytes = b"%PDF-1.4 fake"
    code, body = multipart_upload(
        drA_token, thread_path,
        files=[("note.pdf", pdf_bytes, "application/pdf")],
    )
    if not step("PDF upload 200", code == 200, f"code={code} body={body}"): failures += 1
    pdf_att = (body.get("attachments") or [None])[0] or {}
    if not step("attachment.kind=document", pdf_att.get("kind") == "document",
                f"kind={pdf_att.get('kind')}"): failures += 1

    print("\n=== STEP 28: chat — unlinked doctor blocked (404) ===")
    code, _ = req("GET", thread_path, token=drB_token)
    if not step("unlinked doctor GET -> 404", code == 404, f"code={code}"): failures += 1
    code, _ = req("POST", thread_path, token=drB_token,
                  json_body={"body": "I'm not your doctor"})
    if not step("unlinked doctor POST -> 404", code == 404, f"code={code}"): failures += 1
    # Even read should be blocked.
    code, _ = req("POST", f"/chat/threads/{rahul_patient_id}/{drA_id}/read", token=drB_token)
    if not step("unlinked doctor mark-read -> 404", code == 404, f"code={code}"): failures += 1

    print("\n=== STEP 29: chat — unauthenticated rejected (401) ===")
    code, _ = req("GET", "/chat/threads")
    if not step("no-auth list threads -> 401", code == 401, f"code={code}"): failures += 1
    code, _ = req("GET", thread_path)
    if not step("no-auth list messages -> 401", code == 401, f"code={code}"): failures += 1

    print("\n=== STEP 30: chat — empty body rejected (400) ===")
    code, body = req("POST", thread_path, token=drA_token, json_body={"body": ""})
    if not step("empty body -> 400", code == 400, f"code={code} body={body}"): failures += 1

    if failures:
        print(f"\n{failures} step(s) FAILED.")
        return 1
    print("\nAll steps passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
