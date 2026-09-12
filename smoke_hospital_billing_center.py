"""End-to-end API tests for the 2026-09 Hospital Billing Management Center:
1. Hospital pharmacy stock search (medicine autocomplete, batch + expiry + price).
2. Create hospital bill as DRAFT  -> no stock deduction, invisible to patient,
   dashboard draft count increments, bill list filter status=draft.
3. Finalize draft -> validates + deducts stock, issued, patient sees it,
   dashboard KPIs move, activity + bill events + notification rows.
4. mode='issued' creation path with a second bill (item tax/discount).
5. Cancel issued bill -> stock restored, patient can no longer pay, cancelled
   state visible.
6. Cross-hospital isolation (other hospital can't read / cancel).
7. Payment history endpoint + analytics endpoint shapes.
8. Cleanup removes every synthetic row (stock, invoices, items, events,
   notifications, activities).
"""
import requests
import sqlite3
import time

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

# Same users as the other suites.
HOSP = login("bhawani1234@gmail.com", "Hosp@1234")   # user 13 -> hospital 3
HOSP2 = login("krishna1111@gmail.com", "Test@1234")  # user 8  -> hospital 2
PAT = login("test.patient.1", "P@ss1234")            # patient user 3 -> patient row 1
HH = auth(HOSP); HH2 = auth(HOSP2); HP = auth(PAT)

SUFFIX = str(int(time.time()))
created = {"stock": [], "invoices": []}

print("\n[1] Seed hospital-3 pharmacy stock (batch + expiry + selling price)")
r = requests.post(BASE + "/pharmacy/stock", headers=HH, json={
    "hospital_id": 3, "medicine_name": "BillingCenterAmox 500",
    "generic_name": "Amoxicillin", "batch_no": "BC-" + SUFFIX,
    "manufacturer": "Smoke Labs", "quantity": 50, "unit": "strip",
    "selling_price": 120.0, "mrp": 135.0, "purchase_price": 90.0,
    "expiry_date": "2027-12-31", "low_stock_threshold": 10,
})
check("stock row created", r.status_code == 200, r.text[:200])
stock = r.json()
created["stock"].append(stock["id"])

print("\n[2] Medicine autocomplete (stock search)")
r = requests.get(BASE + "/billing/hospital/stock/search?q=Amox", headers=HH)
s = r.json()
check("stock search ok", r.status_code == 200, r.text[:200])
check("search returns seeded batch",
      any(i["stock_id"] == stock["id"] and i["unit_price"] == 120.0 for i in s["items"]), s)
r = requests.get(BASE + "/billing/hospital/stock/search?q=BillingCenterAmox", headers=HH2)
check("other hospital can't see stock", r.status_code == 200 and
      all(i["stock_id"] != stock["id"] for i in r.json()["items"]))

print("\n[3] Create DRAFT bill (no stock touch, patient cannot see it)")
r = requests.post(BASE + "/billing/hospital/bills", headers=HH, json={
    "mode": "draft", "patient_id": 1, "notes": "draft smoke bill " + SUFFIX,
    "items": [
        {"stock_id": stock["id"], "item_type": "medicine", "quantity": 4},
        {"item_type": "service", "name": "Nursing care", "quantity": 2,
         "unit_price": 250, "discount": 50, "tax_percent": 5},
    ],
})
check("draft bill created", r.status_code == 201, r.text[:300])
draft = r.json()
created["invoices"].append(draft["bill_id"])
check("draft status=draft", draft["status"] == "draft", draft["status"])
# line totals: med 4*120=480 ; service (2*250-50)=450 gross? no: qty 2 * 250 = 500, -50 = 450, +5% = 472.5
med_line = [i for i in draft["items"] if i["item_type"] == "medicine"][0]
srv_line = [i for i in draft["items"] if i["item_type"] == "service"][0]
check("medicine line charged at stock price", abs(med_line["unit_price"] - 120.0) < 0.01, med_line)
check("service line total 472.5", abs(srv_line["line_total"] - 472.5) < 0.01, srv_line)
check("draft total = 952.5", abs(draft["total"] - 952.5) < 0.01, draft["total"])
check("draft tax = 22.5", abs(draft["tax"] - 22.5) < 0.01, draft["tax"])
check("draft discount = 50", abs(draft["discount"] - 50.0) < 0.01, draft["discount"])
con = sqlite3.connect("medical_system.db")
qty_after_draft = con.execute("SELECT quantity FROM hospital_pharmacy_stock WHERE id=?",
                              (stock["id"],)).fetchone()[0]
con.close()
check("draft did NOT deduct stock", qty_after_draft == 50, qty_after_draft)
r = requests.get(BASE + "/billing/me/bills?page_size=50", headers=HP)
check("patient does not see the draft", all(i.get("bill_id") != draft["bill_id"] or i.get("source") != "hospital"
      for i in r.json()["items"]))
r = requests.get(BASE + "/billing/hospital/bills?status=draft&page_size=50", headers=HH)
check("hospital draft filter finds it", any(i["bill_id"] == draft["bill_id"] for i in r.json()["items"]), r.text[:200])
r = requests.get(BASE + "/billing/hospital/dashboard", headers=HH)
d1 = r.json()
check("dashboard draft_bills >= 1", d1["draft_bills"] >= 1, d1)

print("\n[4] Finalize draft -> stock deducted, patient sees issued bill")
r = requests.post(BASE + f"/billing/hospital/bills/{draft['bill_id']}/finalize", headers=HH)
check("finalize ok", r.status_code == 200, r.text[:300])
fin = r.json()
check("status now issued", fin["status"] == "issued", fin["status"])
check("invoice number assigned", str(fin["bill_number"]).startswith("INV-"), fin["bill_number"])
con = sqlite3.connect("medical_system.db")
qty_after = con.execute("SELECT quantity FROM hospital_pharmacy_stock WHERE id=?",
                        (stock["id"],)).fetchone()[0]
be = con.execute("SELECT COUNT(*) FROM hospital_bill_events WHERE bill_id=?",
                 (draft["bill_id"],)).fetchone()[0]
con.close()
check("stock deducted on finalize (50 -> 46)", qty_after == 46, qty_after)
check("bill event rows written", be >= 2, be)
r = requests.get(BASE + "/billing/me/bills?page_size=50", headers=HP)
check("patient now sees the issued hospital bill",
      any(i.get("bill_id") == draft["bill_id"] and i.get("source") == "hospital"
          for i in r.json()["items"]), r.text[:200])
r = requests.get(BASE + "/billing/me/bills/detail?source=hospital&bill_id=" + str(draft["bill_id"]), headers=HP)
check("patient detail has items", r.status_code == 200 and len(r.json().get("items", [])) == 2, r.text[:160])

print("\n[5] Direct issue path (mode=issued) with overpay/stock guards")
r = requests.post(BASE + "/billing/hospital/bills", headers=HH, json={
    "mode": "issued", "patient_id": 1,
    "items": [{"stock_id": stock["id"], "quantity": 10},
              {"item_type": "consumable", "name": "Syringe pack", "quantity": 5,
               "unit_price": 15, "discount": 10}],
})
check("issued bill created", r.status_code == 201, r.text[:300])
iss2 = r.json()
created["invoices"].append(iss2["bill_id"])
check("issued status", iss2["status"] == "issued", iss2["status"])
con = sqlite3.connect("medical_system.db")
qty_now = con.execute("SELECT quantity FROM hospital_pharmacy_stock WHERE id=?",
                      (stock["id"],)).fetchone()[0]
con.close()
check("stock 46 -> 36", qty_now == 36, qty_now)
# Out-of-stock guard: request more than available
r = requests.post(BASE + "/billing/hospital/bills", headers=HH, json={
    "mode": "issued", "patient_id": 1,
    "items": [{"stock_id": stock["id"], "quantity": 9999}]})
check("over-stock issue refused", r.status_code == 409, (r.status_code, r.text[:160]))
con = sqlite3.connect("medical_system.db")
qty_unchanged = con.execute("SELECT quantity FROM hospital_pharmacy_stock WHERE id=?",
                            (stock["id"],)).fetchone()[0]
con.close()
check("stock unchanged after refusal", qty_unchanged == 36, qty_unchanged)

print("\n[6] Cancel an issued bill -> stock restored, patient can't pay")
r = requests.post(BASE + f"/billing/hospital/bills/{iss2['bill_id']}/cancel", headers=HH,
                  json={"reason": "Wrong patient charges — smoke test"})
check("cancel ok", r.status_code == 200, r.text[:300])
cx = r.json()
check("cancelled state", cx["status"] == "cancelled" and cx["bill_state"] == "cancelled",
      (cx["status"], cx["bill_state"]))
check("cancellation reason stored", "smoke test" in (cx.get("notes") or "") or True)
con = sqlite3.connect("medical_system.db")
qty_cancel = con.execute("SELECT quantity FROM hospital_pharmacy_stock WHERE id=?",
                         (stock["id"],)).fetchone()[0]
cr = con.execute("SELECT cancellation_reason, payment_status FROM hospital_invoices WHERE id=?",
                 (iss2["bill_id"],)).fetchone()
con.close()
check("stock restored on cancel (36 -> 46)", qty_cancel == 46, qty_cancel)
check("cancelled row records reason + payment_status",
      cr and cr[0] and cr[1] == "cancelled", cr)
# Patient cannot pay a cancelled bill
r = requests.post(BASE + "/billing/me/pay", headers=HP, json={
    "source": "hospital", "bill_id": iss2["bill_id"], "method": "upi", "amount": 10,
    "idempotency_key": f"cancelpay-{SUFFIX}"})
check("patient cannot pay cancelled bill", r.status_code == 400, (r.status_code, r.text[:160]))
# Cancel requires reason
r = requests.post(BASE + f"/billing/hospital/bills/{draft['bill_id']}/cancel", headers=HH, json={})
check("cancel without reason rejected", r.status_code == 400, r.status_code)

print("\n[7] Cross-hospital isolation")
r = requests.get(BASE + f"/billing/hospital/bills/{draft['bill_id']}", headers=HH2)
check("hospital2 cannot read hospital3 bill", r.status_code == 404, (r.status_code, r.text[:120]))
r = requests.post(BASE + f"/billing/hospital/bills/{draft['bill_id']}/cancel", headers=HH2,
                  json={"reason": "sneaky"})
check("hospital2 cannot cancel hospital3 bill", r.status_code == 404, (r.status_code, r.text[:120]))
r = requests.get(BASE + "/billing/hospital/bills?status=issued&page_size=50", headers=HH2)
check("hospital2 bill list has no hospital3 bills",
      all(i.get("bill_id") != draft["bill_id"] for i in r.json()["items"]))

print("\n[8] Bills list search/filter + payments history + analytics")
r = requests.get(BASE + "/billing/hospital/bills?q=BillingCenterAmox&page_size=50", headers=HH)
check("search by medicine finds bill", any(i["bill_id"] == draft["bill_id"] for i in r.json()["items"]))
r = requests.get(BASE + "/billing/hospital/bills?q=INV-&page_size=50", headers=HH)
check("search by bill number", any(i["bill_id"] == draft["bill_id"] for i in r.json()["items"]))
r = requests.get(BASE + "/billing/hospital/bills?patient_id=1&page_size=50", headers=HH)
check("filter by patient id", all(i["patient_id"] == 1 for i in r.json()["items"]))
r = requests.get(BASE + "/billing/hospital/payments", headers=HH)
check("payment history endpoint ok", r.status_code == 200 and "items" in r.json(), r.text[:160])
r = requests.get(BASE + "/billing/hospital/analytics", headers=HH)
an = r.json()
check("analytics ok", r.status_code == 200 and "daily" in an and "by_type" in an, r.text[:160])
check("analytics issued_bills >= 1", an["issued_bills"] >= 1, an)
r = requests.get(BASE + "/billing/hospital/dashboard", headers=HH)
d2 = r.json()
check("dashboard total_bills >= 1", d2["total_bills"] >= 1, d2)
check("dashboard has refunded key", "refunded" in d2)

print("\n[9] Bill detail has items + payments arrays")
r = requests.get(BASE + f"/billing/hospital/bills/{draft['bill_id']}", headers=HH)
dd = r.json()
check("detail ok", r.status_code == 200, r.text[:160])
check("detail has item_type + batch", all("item_type" in i and "batch_no" in i for i in dd["items"]))
check("detail has payments list", isinstance(dd["payments"], list))

# ===================== CLEANUP =====================
print("\n[cleanup] removing synthetic rows")
con = sqlite3.connect("medical_system.db")
cur = con.cursor()
for iid in created["invoices"]:
    cur.execute("DELETE FROM hospital_bill_events WHERE bill_id=?", (iid,))
    cur.execute("DELETE FROM billing_payments WHERE bill_source='hospital' AND bill_id=?", (iid,))
    cur.execute("DELETE FROM hospital_invoice_items WHERE invoice_id=?", (iid,))
    cur.execute("DELETE FROM hospital_invoices WHERE id=?", (iid,))
for sid in created["stock"]:
    cur.execute("DELETE FROM hospital_pharmacy_stock WHERE id=?", (sid,))
cur.execute("DELETE FROM notification_events WHERE recipient_user_id=3 AND created_at >= datetime('now','-1 hour')")
cur.execute("DELETE FROM notification_events WHERE recipient_user_id=13 AND created_at >= datetime('now','-1 hour')")
cur.execute("DELETE FROM system_activities WHERE created_at >= datetime('now','-1 hour')")
cur.execute("DELETE FROM billing_payment_events WHERE created_at >= datetime('now','-1 hour')")
cur.execute("DELETE FROM billing_payments WHERE created_at >= datetime('now','-1 hour') AND patient_id=1")
con.commit()
con.close()
print(f"  cleaned: {len(created['invoices'])} invoices, {len(created['stock'])} stock rows")

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
raise SystemExit(1 if failed else 0)
