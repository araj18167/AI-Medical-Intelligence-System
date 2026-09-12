"""End-to-end API tests for the 2026-09 chat interaction layer:

1. Messaging: send (JSON + multipart), reply-to previews, idempotent retries.
2. Reactions: add / change / remove, single row per user, authorization.
3. Edit / delete: edit own, edit-other forbidden, delete-for-me vs
   delete-for-everyone (audit row retained).
4. Saved/starred messages + Saved view.
5. Conversation prefs: pin / archive / mute / typing + presence pings.
6. Search: in-conversation and global, plus input validation.
7. Security: unrelated users cannot list threads, read messages, react,
   edit or delete other people's messages.

Uses real records in medical_system.db (patient user 3 <-> doctor user 2).
"""
import json
import sqlite3
import time

import requests

BASE = "http://127.0.0.1:8000"
passed = 0
failed = 0


def ok(cond, label):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {label}")


def login(username, password):
    r = requests.post(BASE + "/login", data={"username": username, "password": password}, timeout=30)
    assert r.status_code == 200, r.text[:200]
    return r.json()["access_token"]


PAT = login("test.patient.1", "P@ss1234")      # user 3, patient row 1
DR = login("dr1", "Test@1234")                 # user 2 (linked doctor)
INTRUDER = login("bhawani5061@gmail.com", "Test@1234")  # shopkeeper user 34 (unrelated)


def H(token, json_body=False):
    h = {"Authorization": "Bearer " + token}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def get(token, path, **params):
    return requests.get(BASE + path, headers=H(token), params=params, timeout=30)


def post(token, path, body=None, raw_json=None):
    if raw_json is not None:
        return requests.post(BASE + path, headers=H(token, True), json=raw_json, timeout=30)
    return requests.post(BASE + path, headers=H(token), data=body or {}, timeout=30)


def patch(token, path, raw_json):
    return requests.patch(BASE + path, headers=H(token, True), json=raw_json, timeout=30)


def put(token, path, raw_json):
    return requests.put(BASE + path, headers=H(token, True), json=raw_json, timeout=30)


def delete(token, path, **params):
    return requests.delete(BASE + path, headers=H(token), params=params, timeout=30)


P = (1, 2)  # thread pair (patient_id, doctor_user_id)

# ---------------------------------------------------------------- messaging
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "hello doctor, base msg"})
ok(r.status_code == 200, "patient send 200")
m_base = r.json()
mid = m_base["id"]

# reply with a preview
r = post(DR, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "Hi, replying", "reply_to_id": mid})
ok(r.status_code == 200 and r.json().get("reply_to") and r.json()["reply_to"]["id"] == mid,
   "reply carries preview of original")
ok((r.json()["reply_to"] or {}).get("body") == "hello doctor, base msg", "reply preview body matches")

# reply target must live in the same thread
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "bad reply", "reply_to_id": 999999})
ok(r.status_code == 400, "invalid reply target rejected")

# idempotent send: same client_message_id -> same message id
payload = {"body": "duplicate-check", "client_message_id": "smoke-dup-1"}
r1 = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json=payload)
r2 = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json=payload)
ok(r1.status_code == 200 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"],
   "client_message_id prevents duplicate sends")

# multipart send (text + file) - a tiny text attachment is allowed
files = {"files": ("note.txt", b"hello attachment", "text/plain")}
r = requests.post(
    BASE + f"/chat/threads/{P[0]}/{P[1]}/messages",
    headers={"Authorization": "Bearer " + PAT},
    data={"body": "with file", "reply_to_id": str(mid)},
    files=files,
    timeout=30,
)
ok(r.status_code == 200 and r.json().get("attachments"), "multipart send with attachment + reply works")
m_file = r.json()

# ---------------------------------------------------------------- reactions
r = post(DR, f"/chat/messages/{mid}/react", raw_json={"reaction": "👍"})
ok(r.status_code == 200 and len(r.json()["reactions"]) == 1, "add reaction")
r = post(DR, f"/chat/messages/{mid}/react", raw_json={"reaction": "❤️"})
ok(r.status_code == 200 and len(r.json()["reactions"]) == 1 and r.json()["reactions"][0]["reaction"] == "❤️",
   "changing reaction keeps a single row per user")
r = post(PAT, f"/chat/messages/{mid}/react", raw_json={"reaction": "😮"})
ok(r.status_code == 200 and len(r.json()["reactions"]) == 2, "second user reacts")
r = delete(DR, f"/chat/messages/{mid}/react")
ok(r.status_code == 200 and len(r.json()["reactions"]) == 1, "remove own reaction")

# unauthorized user cannot react to a thread they are not part of
r = post(INTRUDER, f"/chat/messages/{mid}/react", raw_json={"reaction": "👍"})
ok(r.status_code in (403, 404), "unrelated user cannot react")

# ---------------------------------------------------------------- edit
r = patch(PAT, f"/chat/messages/{mid}", {"body": "hello doctor, edited version"})
ok(r.status_code == 200 and r.json()["edited_at"] is not None, "edit own message marks edited_at")
ok((r.json().get("body") or "").startswith("hello doctor, edited"), "edit changes the body")
r = patch(DR, f"/chat/messages/{mid}", {"body": "stealing your message"})
ok(r.status_code == 403, "cannot edit another user's message")
r = patch(PAT, f"/chat/messages/{mid}", {"body": "   "})
ok(r.status_code == 400, "empty edit rejected")

# ---------------------------------------------------------------- delete
# delete-for-me
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "hide-me-only"})
m_me = r.json()["id"]
r = delete(PAT, f"/chat/messages/{m_me}")
ok(r.status_code == 200 and r.json()["hidden_from_me"] is True, "delete-for-me returns hidden flag")
r = get(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages")
ok(all(m["id"] != m_me for m in r.json()["messages"]), "message hidden from sender's own list")
r = get(DR, f"/chat/threads/{P[0]}/{P[1]}/messages")
ok(any(m["id"] == m_me for m in r.json()["messages"]), "message still visible to the other side")

# delete-for-everyone: sender only + time window
r = delete(DR, f"/chat/messages/{mid}", for_everyone=True)
ok(r.status_code == 403, "non-sender cannot delete for everyone")
r = delete(PAT, f"/chat/messages/{mid}", for_everyone=True)
ok(r.status_code == 200, "sender deletes for everyone")
r = get(DR, f"/chat/threads/{P[0]}/{P[1]}/messages")
ok(all(m["id"] != mid for m in r.json()["messages"]), "deleted-for-everyone gone from other side")
# audit row retained in the DB
con = sqlite3.connect("medical_system.db")
row = con.execute("SELECT id, body, deleted_at, deleted_by_user_id FROM chat_messages WHERE id=?", (mid,)).fetchone()
con.close()
ok(row is not None and row[2] is not None and row[3] == 3, "delete-for-everyone keeps an audit row")

# ---------------------------------------------------------------- saved
r = post(PAT, f"/chat/messages/{m_file['id']}/save")
ok(r.status_code == 200 and r.json()["saved_by_me"] is True, "star/save message")
r = post(PAT, f"/chat/messages/{m_file['id']}/save")
ok(r.status_code == 200 and r.json()["saved_by_me"] is True, "save is idempotent")
r = get(PAT, "/chat/saved")
saved_items = r.json()["items"] if r.status_code == 200 else []
ok(r.status_code == 200 and any(i["message"]["id"] == m_file["id"] for i in saved_items),
   "saved list shows the starred message")
r = delete(PAT, f"/chat/messages/{m_file['id']}/save")
ok(r.status_code == 200 and r.json()["saved_by_me"] is False, "unstar message")

# ------------------------------------------------- prefs: pin/archive/mute/typing
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/pin", raw_json={"pinned": True})
ok(r.status_code == 200 and r.json()["pinned"] is True, "pin conversation")
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/archive", raw_json={"archived": True})
ok(r.status_code == 200 and r.json()["archived"] is True, "archive conversation")
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/mute", raw_json={"minutes": 60})
ok(r.status_code == 200 and r.json()["muted"] is True, "mute conversation")
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/typing", raw_json={"typing": True})
ok(r.status_code == 200, "typing ping accepted")

# threads listing reflects the prefs for the caller
r = get(PAT, "/chat/threads")
threads = r.json()
t0 = next(t for t in threads if t["patient_id"] == P[0] and t["doctor_user_id"] == P[1])
ok(t0["pinned"] is True and t0["archived"] is True and t0["muted"] is True, "thread exposes pin/archive/mute")

# doctor sees typing + online from the patient ping
r = get(DR, "/chat/threads")
td = next(t for t in r.json() if t["patient_id"] == P[0] and t["doctor_user_id"] == P[1])
ok(td.get("partner_typing") is True, "partner typing indicator visible to other side")
ok(td.get("partner_online") is True, "partner presence online after ping")

# unmute + unpin + unarchive
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/mute", raw_json={"minutes": 0})
ok(r.status_code == 200 and r.json()["muted"] is False, "unmute conversation")
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/pin", raw_json={"pinned": False})
ok(r.json()["pinned"] is False, "unpin conversation")
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/archive", raw_json={"archived": False})
ok(r.json()["archived"] is False, "unarchive conversation")

# presence ping is harmless and returns ok
r = post(PAT, "/chat/presence/ping")
ok(r.status_code == 200 and r.json().get("ok") is True, "presence ping ok")

# mute suppresses a notification push (verify no new chat_message notification
# is created for the muted side while the other side types/sends)
post(PAT, f"/chat/threads/{P[0]}/{P[1]}/mute", raw_json={"minutes": 60})
before = get(DR, "/notifications/recent").json().get("items") or []
post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "muted silence please"})
after = get(DR, "/notifications/recent").json().get("items") or []
ok(len(after) == len(before), "muted conversation does not push a new notification")
post(PAT, f"/chat/threads/{P[0]}/{P[1]}/mute", raw_json={"minutes": 0})

# ---------------------------------------------------------------- search
r = get(PAT, f"/chat/threads/{P[0]}/{P[1]}/search", q="duplicate")
ok(r.status_code == 200 and r.json()["total"] >= 1, "in-conversation search finds text")
r = get(PAT, f"/chat/threads/{P[0]}/{P[1]}/search", q="x")
ok(r.status_code == 400, "too-short query rejected")
r = get(PAT, "/chat/search", q="duplicate")
ok(r.status_code == 200 and r.json()["total"] >= 1, "global search returns matching messages")
r = get(PAT, "/chat/search", q="duplicate")
ok(r.json()["items"] and r.json()["items"][0]["thread"]["patient_id"] == P[0], "global search groups by thread")

# ---------------------------------------------------------------- security
r = get(INTRUDER, "/chat/threads")
ok(r.status_code == 200 and len(r.json()) == 0, "unrelated role has no chat threads")
r = get(INTRUDER, f"/chat/threads/{P[0]}/{P[1]}/messages")
ok(r.status_code in (403, 404), "unrelated role cannot read a private thread")
r = post(INTRUDER, f"/chat/messages/{m_me}/save")
ok(r.status_code in (403, 404), "unrelated role cannot star a private message")
r = delete(INTRUDER, f"/chat/messages/{m_me}")
ok(r.status_code in (403, 404), "unrelated role cannot delete a private message")
r = patch(INTRUDER, f"/chat/messages/{m_me}", {"body": "hacked"})
ok(r.status_code in (403, 404), "unrelated role cannot edit a private message")
r = get(INTRUDER, "/chat/saved")
ok(r.status_code == 200 and r.json()["items"] == [], "unrelated role sees only their own (empty) saved list")

# ================================================================
# 2026-09 additions: forward, per-user privacy, audio attachments
# ================================================================
NURSE = login("nurse2smoke1786942367", "Nurse@1234")   # user 16, caring for patient 1
NPAIR = (1, 16)  # patient <-> nurse thread (second conversation for PAT)

# ---- forward: text copy -----------------------------------------
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "forward-me-src"})
ok(r.status_code == 200, "send source message for forward")
src = r.json()
r = post(PAT, f"/chat/messages/{src['id']}/forward", raw_json={"patient_id": NPAIR[0], "doctor_user_id": NPAIR[1]})
ok(r.status_code == 200, "forward text message to another conversation")
fwd = r.json()
ok(fwd["id"] != src["id"], "forward creates a new message id")
ok(fwd["body"] == "forward-me-src", "forward copies the original body")
ok(fwd["patient_id"] == NPAIR[0] and fwd["doctor_user_id"] == NPAIR[1], "forward lands in the target thread")
r = get(NURSE, f"/chat/threads/{NPAIR[0]}/{NPAIR[1]}/messages")
ok(any(m["id"] == fwd["id"] for m in r.json()["messages"]), "target participant sees the forwarded message")

# forward with a note -> body = original + note
r = post(PAT, f"/chat/messages/{src['id']}/forward",
         raw_json={"patient_id": NPAIR[0], "doctor_user_id": NPAIR[1], "body": "see above pls"})
ok(r.status_code == 200 and "forward-me-src" in (r.json().get("body") or "")
   and "see above pls" in (r.json().get("body") or ""), "forward accepts an appended note")

# idempotent forward via client_message_id
cid = "smoke-fwd-idem-1"
b1 = post(PAT, f"/chat/messages/{src['id']}/forward",
          raw_json={"patient_id": NPAIR[0], "doctor_user_id": NPAIR[1], "client_message_id": cid})
b2 = post(PAT, f"/chat/messages/{src['id']}/forward",
          raw_json={"patient_id": NPAIR[0], "doctor_user_id": NPAIR[1], "client_message_id": cid})
ok(b1.status_code == 200 and b2.status_code == 200 and b1.json()["id"] == b2.json()["id"],
   "forward retries with the same key do not duplicate")

# forward with attachment -> attachments cloned into the target thread
files = {"files": ("fwd-report.txt", b"forwarded file bytes", "text/plain")}
r = requests.post(BASE + f"/chat/threads/{P[0]}/{P[1]}/messages",
                  headers={"Authorization": "Bearer " + PAT},
                  data={"body": "attach me"}, files=files, timeout=30)
ok(r.status_code == 200 and r.json().get("attachments"), "send attachment message to forward")
msrc = r.json()
r = post(PAT, f"/chat/messages/{msrc['id']}/forward",
         raw_json={"patient_id": NPAIR[0], "doctor_user_id": NPAIR[1]})
ok(r.status_code == 200, "forward attachment message")
fwda = r.json()
ok(len(fwda.get("attachments", [])) == 1
   and fwda["attachments"][0]["file_name"] == "fwd-report.txt", "forwarded message clones attachments")
att = fwda["attachments"][0]
r = requests.get(BASE + att["download_url"], headers={"Authorization": "Bearer " + NURSE}, timeout=30)
ok(r.status_code == 200 and r.content == b"forwarded file bytes", "target participant downloads a forwarded attachment")

# forward guards
r = post(PAT, f"/chat/messages/{src['id']}/forward",
         raw_json={"patient_id": P[0], "doctor_user_id": P[1]})
ok(r.status_code == 400, "forwarding into the same conversation is rejected")
r = post(INTRUDER, f"/chat/messages/{src['id']}/forward",
         raw_json={"patient_id": NPAIR[0], "doctor_user_id": NPAIR[1]})
ok(r.status_code in (403, 404), "unrelated user cannot forward another person's message")
r = post(PAT, f"/chat/messages/{src['id']}/forward",
         raw_json={"patient_id": 7, "doctor_user_id": 2})
ok(r.status_code in (403, 404), "cannot forward into a conversation the caller does not belong to")

# ---- per-user privacy --------------------------------------------
r = get(PAT, "/chat/privacy")
ok(r.status_code == 200 and r.json()["show_last_seen"] is True and r.json()["show_typing"] is True
   and r.json()["show_read_receipts"] is True, "privacy defaults all-visible")
r = put(PAT, "/chat/privacy", {"show_last_seen": False, "show_typing": False})
ok(r.status_code == 200 and r.json()["show_last_seen"] is False and r.json()["show_typing"] is False,
   "privacy partial update works")

# partner presence + typing are hidden when the partner turned them off
post(PAT, f"/chat/threads/{P[0]}/{P[1]}/typing", raw_json={"typing": True})
post(PAT, "/chat/presence/ping")
r = get(DR, "/chat/threads")
td = next(t for t in r.json() if t["patient_id"] == P[0] and t["doctor_user_id"] == P[1])
ok(td.get("partner_typing") is False and td.get("partner_online") is False
   and td.get("partner_last_seen") is None, "privacy hides typing + presence from the other side")

# re-enable -> visible again
put(PAT, "/chat/privacy", {"show_last_seen": True, "show_typing": True})
post(PAT, f"/chat/threads/{P[0]}/{P[1]}/typing", raw_json={"typing": True})
r = get(DR, "/chat/threads")
td = next(t for t in r.json() if t["patient_id"] == P[0] and t["doctor_user_id"] == P[1])
ok(td.get("partner_typing") is True and td.get("partner_online") is True,
   "re-enabled privacy exposes typing + presence again")

# read receipts: patient sees ✓✓ once the doctor reads...
r = post(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages", raw_json={"body": "read-receipt-check"})
rid = r.json()["id"]
post(DR, f"/chat/threads/{P[0]}/{P[1]}/read")
r = get(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages")
mrow = next(m for m in r.json()["messages"] if m["id"] == rid)
ok(mrow.get("read_by_other") is True, "sender sees read tick after the other side read")
# ...and hides it when the reader turns read receipts off
put(DR, "/chat/privacy", {"show_read_receipts": False})
r = get(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages")
mrow = next(m for m in r.json()["messages"] if m["id"] == rid)
ok(mrow.get("read_by_other") is None, "read receipts off hides the read tick from the sender")
put(DR, "/chat/privacy", {"show_read_receipts": True})
r = get(PAT, f"/chat/threads/{P[0]}/{P[1]}/messages")
mrow = next(m for m in r.json()["messages"] if m["id"] == rid)
ok(mrow.get("read_by_other") is True, "re-enabling read receipts restores the tick")

# ---- audio attachments -------------------------------------------
files = {"files": ("voice-note.webm", b"\x1aE\xdf\xa3fakewebm", "audio/webm")}
r = requests.post(BASE + f"/chat/threads/{P[0]}/{P[1]}/messages",
                  headers={"Authorization": "Bearer " + PAT},
                  data={"body": "audio test"}, files=files, timeout=30)
ok(r.status_code == 200, "audio upload accepted")
aud_msg = r.json()
aud = aud_msg["attachments"][0]
ok(aud["kind"] == "audio" and aud["mime_type"] == "audio/webm", "audio attachment classified as kind=audio")
r = requests.get(BASE + aud["download_url"], headers={"Authorization": "Bearer " + DR}, timeout=30)
ok(r.status_code == 200, "thread participant can stream the audio attachment")
r = requests.get(BASE + aud["download_url"], headers={"Authorization": "Bearer " + INTRUDER}, timeout=30)
ok(r.status_code in (403, 404), "unrelated user cannot download the audio attachment")

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
raise SystemExit(1 if failed else 0)
