"""
Database tables (models).
Each class here becomes one table in medical_system.db
"""
from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Text, Float, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


# ---------- Doctor profile (rich data on top of User) ----------

class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    specialization = Column(String, nullable=True)         # e.g. "Cardiologist"
    qualifications = Column(Text, nullable=True)           # e.g. "MBBS, MD (Internal Medicine)"
    experience_years = Column(Integer, nullable=True)
    clinic_name = Column(String, nullable=True)
    clinic_address = Column(Text, nullable=True)
    consultation_fee = Column(String, nullable=True)       # keep as string to allow currency symbols
    bio = Column(Text, nullable=True)
    languages = Column(String, nullable=True)              # comma-separated, e.g. "English, Hindi"
    available_hours = Column(String, nullable=True)        # free text, e.g. "Mon-Fri 10am-5pm"
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================


class OPDVisit(Base):
    """One row per outpatient visit. OPD = patient walks in, sees a doctor,
    gets a prescription, and leaves the same day (no admission/bed)."""
    __tablename__ = "opd_visits"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    visit_number = Column(String, nullable=False)           # auto-generated, e.g. OPD-20260825-001
    department = Column(String, nullable=True)              # "Cardiology", "General", etc.
    chief_complaint = Column(Text, nullable=True)           # why the patient came
    symptoms = Column(Text, nullable=True)
    diagnosis = Column(Text, nullable=True)
    treatment_given = Column(Text, nullable=True)
    prescriptions = Column(Text, nullable=True)             # free-text or JSON
    advice = Column(Text, nullable=True)                    # doctor's advice
    follow_up_date = Column(Date, nullable=True)
    consultation_fee = Column(Float, nullable=True)
    fee_paid = Column(Boolean, default=False, nullable=False)
    payment_method = Column(String, nullable=True)          # cash, upi, card
    status = Column(String, nullable=False, default="completed", index=True)
    # "scheduled" | "in_progress" | "completed" | "cancelled" | "no_show"
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# IPD MANAGEMENT (Inpatient Department) — extends existing Admission model
# ===========================================================================
class IPDDiagnosis(Base):
    """Structured diagnosis record for an inpatient admission.
    Multiple diagnoses can be linked to one admission."""
    __tablename__ = "ipd_diagnoses"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    diagnosis_type = Column(String, nullable=True)          # "primary" | "secondary" | "differential"
    icd_code = Column(String, nullable=True)               # ICD-10 code
    description = Column(Text, nullable=False)
    diagnosed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    diagnosed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_resolved = Column(Boolean, default=False, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IPDDailyRecord(Base):
    """Daily clinical record for an inpatient (vitals, progress notes,
    rounds). One row per day per admission."""
    __tablename__ = "ipd_daily_records"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    record_date = Column(Date, nullable=False, index=True)
    # Vitals
    temperature = Column(Float, nullable=True)
    pulse = Column(Integer, nullable=True)
    bp_systolic = Column(Integer, nullable=True)
    bp_diastolic = Column(Integer, nullable=True)
    respiratory_rate = Column(Integer, nullable=True)
    spo2 = Column(Float, nullable=True)                    # oxygen saturation %
    weight_kg = Column(Float, nullable=True)
    # Clinical
    progress_notes = Column(Text, nullable=True)
    diet = Column(String, nullable=True)                   # "normal", "liquid", "NPO"
    output_urine_ml = Column(Integer, nullable=True)
    iv_fluids = Column(Text, nullable=True)
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ===========================================================================
# LABORATORY MANAGEMENT
# ===========================================================================
class LabTestCatalog(Base):
    """Master list of available lab tests (e.g. CBC, Blood Sugar, LFT)."""
    __tablename__ = "lab_test_catalog"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)
    name = Column(String, nullable=False)                  # "Complete Blood Count"
    code = Column(String, nullable=True)                   # "CBC", "FBS"
    category = Column(String, nullable=True)               # "hematology", "biochemistry"
    specimen_type = Column(String, nullable=True)          # "blood", "urine", "stool"
    turnaround_hours = Column(Integer, nullable=True)
    price = Column(Float, nullable=True)
    reference_range = Column(Text, nullable=True)          # JSON: {"Hemoglobin": "12-16", ...}
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class LabOrder(Base):
    """A doctor orders one or more lab tests for a patient."""
    __tablename__ = "lab_orders"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    ordered_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id"), nullable=True, index=True)  # null for OPD
    opd_visit_id = Column(Integer, ForeignKey("opd_visits.id"), nullable=True, index=True)  # null for IPD
    order_number = Column(String, nullable=False)          # LAB-20260825-001
    priority = Column(String, nullable=False, default="routine")  # "routine" | "urgent" | "stat"
    clinical_notes = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="ordered", index=True)
    # "ordered" | "sample_collected" | "in_progress" | "completed" | "cancelled"
    ordered_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    sample_collected_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class LabOrderItem(Base):
    """Individual test within an order."""
    __tablename__ = "lab_order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("lab_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    test_catalog_id = Column(Integer, ForeignKey("lab_test_catalog.id"), nullable=True)
    test_name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="ordered")
    result_id = Column(Integer, ForeignKey("lab_results.id"), nullable=True)  # backlink once result is ready
    created_at = Column(DateTime, default=datetime.utcnow)


class LabResult(Base):
    """Result for a single lab test."""
    __tablename__ = "lab_results"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("lab_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    order_item_id = Column(Integer, ForeignKey("lab_order_items.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    test_name = Column(String, nullable=False)
    result_value = Column(Text, nullable=True)             # the actual value
    result_numeric = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    reference_range = Column(String, nullable=True)
    flag = Column(String, nullable=True)                   # "normal" | "low" | "high" | "critical"
    notes = Column(Text, nullable=True)
    performed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    result_file_name = Column(String, nullable=True)
    result_stored_path = Column(String, nullable=True)
    status = Column(String, nullable=False, default="preliminary")
    # "preliminary" | "final" | "amended"
    created_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime, nullable=True)


# ===========================================================================
# RADIOLOGY MANAGEMENT
# ===========================================================================
class RadiologyExamCatalog(Base):
    """Master list of radiology exams (X-ray, CT, MRI, Ultrasound)."""
    __tablename__ = "radiology_exam_catalog"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)
    name = Column(String, nullable=False)                  # "Chest X-ray PA View"
    modality = Column(String, nullable=False)              # "xray" | "ct" | "mri" | "ultrasound" | "fluoroscopy"
    body_part = Column(String, nullable=True)              # "chest", "abdomen", "brain"
    price = Column(Float, nullable=True)
    turnaround_hours = Column(Integer, nullable=True)
    preparation_notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RadiologyOrder(Base):
    """A doctor orders a radiology exam."""
    __tablename__ = "radiology_orders"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    ordered_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id"), nullable=True, index=True)
    opd_visit_id = Column(Integer, ForeignKey("opd_visits.id"), nullable=True, index=True)
    exam_catalog_id = Column(Integer, ForeignKey("radiology_exam_catalog.id"), nullable=True)
    exam_name = Column(String, nullable=False)
    modality = Column(String, nullable=True)
    body_part = Column(String, nullable=True)
    clinical_history = Column(Text, nullable=True)
    order_number = Column(String, nullable=False)          # RAD-20260825-001
    priority = Column(String, nullable=False, default="routine")
    status = Column(String, nullable=False, default="ordered", index=True)
    # "ordered" | "scheduled" | "in_progress" | "completed" | "cancelled"
    scheduled_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class RadiologyReport(Base):
    """Radiologist's report for an exam."""
    __tablename__ = "radiology_reports"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("radiology_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    findings = Column(Text, nullable=True)
    impression = Column(Text, nullable=True)
    recommendation = Column(Text, nullable=True)
    report_file_name = Column(String, nullable=True)
    report_stored_path = Column(String, nullable=True)
    image_file_name = Column(String, nullable=True)
    image_stored_path = Column(String, nullable=True)
    performed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reported_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, default="pending")
    # "pending" | "preliminary" | "final"
    created_at = Column(DateTime, default=datetime.utcnow)
    reported_at = Column(DateTime, nullable=True)


# ===========================================================================
# INSURANCE MANAGEMENT
# ===========================================================================
class InsuranceProvider(Base):
    """Insurance company master list."""
    __tablename__ = "insurance_providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    contact_phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    website = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class InsurancePolicy(Base):
    """A patient's insurance policy. Multiple policies possible
    (primary + secondary coverage)."""
    __tablename__ = "insurance_policies"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    provider_id = Column(Integer, ForeignKey("insurance_providers.id"), nullable=False, index=True)
    policy_number = Column(String, nullable=False)
    policyholder_name = Column(String, nullable=True)
    relationship_to_patient = Column(String, nullable=True)  # "self", "spouse", "parent"
    plan_type = Column(String, nullable=True)                 # "individual", "family", "group"
    sum_insured = Column(Float, nullable=True)
    deductible = Column(Float, nullable=True)
    valid_from = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InsuranceClaim(Base):
    """A claim filed against a patient's policy for an admission or OPD visit."""
    __tablename__ = "insurance_claims"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    policy_id = Column(Integer, ForeignKey("insurance_policies.id"), nullable=False, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id"), nullable=True, index=True)
    opd_visit_id = Column(Integer, ForeignKey("opd_visits.id"), nullable=True, index=True)
    claim_number = Column(String, nullable=False)          # CLM-20260825-001
    total_amount = Column(Float, nullable=False, default=0.0)
    approved_amount = Column(Float, nullable=True)
    rejected_amount = Column(Float, nullable=True)
    diagnosis_code = Column(String, nullable=True)         # ICD-10
    treatment_description = Column(Text, nullable=True)
    documents_json = Column(Text, nullable=True)           # JSON list of uploaded doc paths
    status = Column(String, nullable=False, default="draft", index=True)
    # "draft" | "submitted" | "under_review" | "approved" | "partially_approved" | "rejected" | "paid"
    submitted_at = Column(DateTime, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# PHARMACY / HOSPITAL INVENTORY (hospital-level, not shopkeeper-level)
# ===========================================================================
class HospitalPharmacyStock(Base):
    """Hospital pharmacy medicine stock. Different from MedicineStock
    (which is per-shopkeeper). This is the hospital's own pharmacy."""
    __tablename__ = "hospital_pharmacy_stock"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    medicine_name = Column(String, nullable=False, index=True)
    generic_name = Column(String, nullable=True)
    batch_no = Column(String, nullable=False)
    manufacturer = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False, default=0)
    unit = Column(String, nullable=True)                   # strip, piece, bottle
    purchase_price = Column(Float, nullable=True)
    mrp = Column(Float, nullable=True)
    selling_price = Column(Float, nullable=True)
    expiry_date = Column(Date, nullable=True)
    low_stock_threshold = Column(Integer, nullable=False, default=10)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HospitalInvoice(Base):
    """Invoice issued by the hospital pharmacy to a patient."""
    __tablename__ = "hospital_invoices"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id"), nullable=True, index=True)
    opd_visit_id = Column(Integer, ForeignKey("opd_visits.id"), nullable=True, index=True)
    invoice_number = Column(String, nullable=False)        # INV-20260825-001
    subtotal = Column(Float, nullable=False, default=0.0)
    discount = Column(Float, nullable=False, default=0.0)
    tax = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)
    payment_method = Column(String, nullable=True)
    payment_status = Column(String, nullable=False, default="pending", index=True)
    # "pending" | "paid" | "partial" | "cancelled"
    # 2026-09 hospital billing center: bill lifecycle on top of payment
    # status. Legacy rows keep NULL and are treated as "issued" everywhere.
    # "draft" | "issued" | "cancelled" | NULL(=issued)
    status = Column(String, nullable=True, index=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    issued_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HospitalInvoiceItem(Base):
    """Line items on a hospital invoice."""
    __tablename__ = "hospital_invoice_items"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("hospital_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("hospital_pharmacy_stock.id"), nullable=True)
    medicine_name = Column(String, nullable=False)
    # 2026-09 billing center: item category + batch snapshot + per-line
    # discount/tax. All nullable for back-compat with older line items.
    item_type = Column(String, nullable=True, default="medicine")  # medicine|equipment|consumable|service|custom
    batch_no = Column(String, nullable=True)
    discount = Column(Float, nullable=False, default=0.0)
    tax_percent = Column(Float, nullable=False, default=0.0)
    tax_amount = Column(Float, nullable=False, default=0.0)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    mrp = Column(Float, nullable=True)
    line_total = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class HospitalBillEvent(Base):
    """Append-only audit trail for the hospital billing lifecycle
    (draft → issued → cancelled) mirroring the BillingPaymentEvent ledger.
    Amounts are not stored here — the bill row + payment ledger are the
    source of truth; this table only records who changed what and when."""
    __tablename__ = "hospital_bill_events"

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey("hospital_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_role = Column(String, nullable=True)
    action = Column(String, nullable=False)   # created | updated | issued | cancelled
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ===========================================================================
# PURCHASE MANAGEMENT
# ===========================================================================
class PurchaseOrder(Base):
    """Hospital purchases medicines / supplies from suppliers."""
    __tablename__ = "purchase_orders"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    order_number = Column(String, nullable=False)          # PO-20260825-001
    supplier_name = Column(String, nullable=True)
    supplier_contact = Column(String, nullable=True)
    total_amount = Column(Float, nullable=False, default=0.0)
    discount = Column(Float, nullable=False, default=0.0)
    tax = Column(Float, nullable=False, default=0.0)
    grand_total = Column(Float, nullable=False, default=0.0)
    payment_method = Column(String, nullable=True)
    payment_status = Column(String, nullable=False, default="pending", index=True)
    # "pending" | "paid" | "partial"
    order_status = Column(String, nullable=False, default="draft", index=True)
    # "draft" | "ordered" | "partial_received" | "received" | "cancelled"
    expected_date = Column(Date, nullable=True)
    received_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PurchaseOrderItem(Base):
    """Individual items in a purchase order."""
    __tablename__ = "purchase_order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    medicine_name = Column(String, nullable=False)
    generic_name = Column(String, nullable=True)
    batch_no = Column(String, nullable=True)
    manufacturer = Column(String, nullable=True)
    quantity_ordered = Column(Integer, nullable=False)
    quantity_received = Column(Integer, nullable=False, default=0)
    unit = Column(String, nullable=True)
    purchase_price = Column(Float, nullable=False)
    mrp = Column(Float, nullable=True)
    expiry_date = Column(Date, nullable=True)
    line_total = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)





# ---------- Patient profile (personal info, separated from core Patient row) ----------

class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), unique=True, nullable=False)
    date_of_birth = Column(DateTime, nullable=True)
    blood_group = Column(String, nullable=True)            # "A+", "O-", etc.
    address = Column(Text, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    country = Column(String, nullable=True)
    pincode = Column(String, nullable=True)
    emergency_contact_name = Column(String, nullable=True)
    emergency_contact_phone = Column(String, nullable=True)
    emergency_contact_relation = Column(String, nullable=True)
    occupation = Column(String, nullable=True)
    marital_status = Column(String, nullable=True)
    # Lifestyle / habits
    smoking = Column(String, nullable=True)                # "never" | "former" | "occasional" | "regular"
    alcohol = Column(String, nullable=True)                # "never" | "occasional" | "regular"
    diet = Column(String, nullable=True)                    # "vegetarian" | "non-vegetarian" | "vegan" | etc.
    exercise = Column(String, nullable=True)                # free text, e.g. "3x/week walking"
    sleep_hours = Column(String, nullable=True)            # free text, e.g. "6-7 hrs"
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class MedicalHistoryEntry(Base):
    __tablename__ = "medical_history"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    # Categories: condition, allergy, surgery, medication, family_history, immunization, other
    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    diagnosed_year = Column(Integer, nullable=True)
    is_ongoing = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- Uploaded medical reports ----------

class PatientReport(Base):
    __tablename__ = "patient_reports"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    # Categories: lab, imaging, discharge, prescription, other
    category = Column(String, nullable=True)
    file_name = Column(String, nullable=False)             # original filename from the upload
    stored_path = Column(String, nullable=False)           # path on disk, e.g. "uploads/12_blood_test.pdf"
    mime_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)             # bytes
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)


class AiReport(Base):
    """Auto-generated AI report — saved from MediScan, Symptom Checker,
    AI Doctor, MediEcho, and other AI features.  One row per analysis."""
    __tablename__ = "ai_reports"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    # "mediscan" | "symptom_checker" | "ai_doctor" | "mediecho" | "prescription_analysis" | "lab_analysis" | "imaging" | "other"
    feature_type = Column(String, nullable=False, index=True)
    # Free-text label like "MediScan Report", "Symptom Checker Report"
    report_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    generated_result = Column(Text, nullable=True)           # full AI output (JSON or text)
    findings = Column(Text, nullable=True)
    recommendations = Column(Text, nullable=True)
    # JSON string with input parameters (symptoms, image path, etc.)
    input_summary = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="completed")  # "completed" | "failed" | "pending"
    ai_model = Column(String, nullable=True)                 # e.g. "gemini-2.0-flash"
    ai_model_version = Column(String, nullable=True)
    source_entity_type = Column(String, nullable=True)       # "session" | "upload" | "analysis"
    source_entity_id = Column(Integer, nullable=True)        # id of the AI session/analysis
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=True)
    # Nullable: Google-only users have no password.
    hashed_password = Column(String, nullable=True)
    role = Column(String, default="patient")  # "patient", "doctor", or "nurse"
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    # When a doctor's "Add patient" flow auto-generates this user account, we
    # record which doctor did it. Lets the doctor see + reset the credentials
    # later. Nullable so existing rows and self-signups are unaffected.
    created_by_doctor_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Google Sign-in fields (both nullable — only populated for Google-linked users)
    google_sub = Column(String, unique=True, nullable=True, index=True)
    google_email = Column(String, nullable=True)
    # Profile picture — relative path under UPLOAD_DIR, e.g. "avatars/3.jpg". Null = use initials.
    profile_picture = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    age = Column(Integer)
    gender = Column(String)
    contact = Column(String)
    # PatientRecords (2026-08). Aadhaar is a national identity number
    # (XXXX-XXXX-XXXX) — stored as plain string since SQLite has no native
    # encryption and the surrounding access checks already gate queries.
    # Indexed so doctor/hospital/nurse search by Aadhaar returns fast.
    aadhar_number = Column(String, nullable=True, index=True)
    # Free-text locality. Used by the PatientRecords "search by place"
    # filter — combines city + state so partial matches still surface the
    # patient. Nullable so legacy rows without it keep working.
    place = Column(String, nullable=True, index=True)
    # DEPRECATED. New code should write to PatientDoctorLink instead. Kept on
    # disk for one release so older DBs can still be read; new endpoints
    # should ignore it. The doctor dashboard scopes ownership via
    # PatientDoctorLink joined on doctor_user_id.
    onboarded_by_doctor_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    records = relationship("MedicalRecord", back_populates="patient")
    prescriptions = relationship("Prescription", back_populates="patient")


class MedicalRecord(Base):
    __tablename__ = "medical_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    symptoms = Column(Text)
    diagnosis = Column(Text)      # e.g. Gemini's predicted conditions, added Day 4
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="records")


class Prescription(Base):
    __tablename__ = "prescriptions"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    medicine_name = Column(String)
    dosage = Column(String)
    instructions = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="prescriptions")


class VitalRecord(Base):
    __tablename__ = "vital_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    blood_pressure_systolic = Column(Integer, nullable=True)
    blood_pressure_diastolic = Column(Integer, nullable=True)
    blood_sugar = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"))
    medicine_name = Column(String, nullable=False)
    dosage = Column(String, nullable=True)
    times = Column(String, nullable=False)   # e.g. "08:00,14:00,20:00"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- MediScan document analysis ----------

class DocumentScan(Base):
    """One row per uploaded medical document that Gemini analyzed.

    doc_type: "prescription" | "lab_report" | "imaging"
    extracted_json: full Gemini response as a JSON string (for re-rendering later)
    summary: plain-language patient-friendly summary
    warnings: JSON list of strings (parse issues, blurry image notes, etc.)
    """
    __tablename__ = "document_scans"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    doc_type = Column(String, nullable=False, index=True)
    file_name = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    mime_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    extracted_json = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    warnings = Column(Text, nullable=True)   # JSON-encoded list
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class LabValue(Base):
    """One row per individual test value extracted from a lab_report DocumentScan.

    value_numeric is set when the result parses to a number; otherwise value_text
    holds the raw result (e.g. "Positive", "Negative", "Reactive").
    """
    __tablename__ = "lab_values"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("document_scans.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    test_name = Column(String, nullable=False)
    value_numeric = Column(Float, nullable=True)
    value_text = Column(String, nullable=True)
    unit = Column(String, nullable=True)
    reference_range = Column(String, nullable=True)
    flag = Column(String, nullable=True)   # "low" | "normal" | "high" | "unknown"
    recorded_at = Column(DateTime, default=datetime.utcnow)


# ---------- Notification preferences ----------
# One row per user; tracks which notification channels they want and how early
# medicine reminders should fire. Created lazily the first time a user touches
# the Settings page so it never blocks other endpoints.

class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True, index=True)
    # unique=True makes this a one-to-one table: one preference row per user.
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    email_enabled = Column(Boolean, default=False, nullable=False)
    browser_enabled = Column(Boolean, default=False, nullable=False)
    # How many minutes before a scheduled reminder we should notify the user.
    reminder_lead_minutes = Column(Integer, default=15, nullable=False)
    # Per-feature opt-out switches for real-time event notifications surfaced
    # in the dashboard bell. Defaults to True so out-of-the-box behavior
    # matches the user's "real-time of any activity" ask — Settings is where
    # they dial things down. Migration in database.py adds these columns to
    # pre-existing tables.
    notif_patient_added = Column(Boolean, default=True, nullable=False)
    notif_doctor_linked = Column(Boolean, default=True, nullable=False)
    notif_chat_message = Column(Boolean, default=True, nullable=False)
    # Buckets all hospital-admit / nurse-assignment / discharge-appeal
    # notifications under one opt-out. Adding one flag instead of seven
    # keeps the migration footprint small; the settings UI can split
    # them later if users ask for finer-grained control.
    notif_admission_events = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class NotificationEvent(Base):
    __tablename__ = "notification_events"

    id = Column(Integer, primary_key=True, index=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # "patient_added" | "doctor_linked" | "chat_message"
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    # Relative URL on the frontend (e.g. "dashboard.html?chat=12,5").
    link = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    read_at = Column(DateTime, nullable=True)


# ---------- Doctor extended profile ----------
# One-to-many child tables. Each row belongs to a doctor user via user_id.
# Pattern follows the existing MedicalHistoryEntry / PatientReport tables.

class DoctorWorkHistory(Base):
    """Career timeline. Years are integers (not full dates) because some
    positions span decades and exact months/days are rarely meaningful."""
    __tablename__ = "doctor_work_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String, nullable=False)               # "Resident", "Consultant", "Head of Dept"
    organization = Column(String, nullable=False)       # "Apollo Hospital"
    location = Column(String, nullable=True)            # "Delhi, India"
    start_year = Column(Integer, nullable=True)
    end_year = Column(Integer, nullable=True)           # null = "Present"
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class DoctorEducation(Base):
    """Degrees and certifications. End_year null means "in progress"."""
    __tablename__ = "doctor_education"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    degree = Column(String, nullable=False)             # "MBBS", "MD Cardiology", "FRCS"
    institution = Column(String, nullable=False)        # "AIIMS, New Delhi"
    start_year = Column(Integer, nullable=True)
    end_year = Column(Integer, nullable=True)           # null = "In progress"
    description = Column(Text, nullable=True)            # thesis / honors / details
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class DoctorAddress(Base):
    """Practice / clinic / hospital addresses. is_primary marks the default."""
    __tablename__ = "doctor_addresses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    label = Column(String, nullable=True)                # "Primary clinic", "Hospital", "Home"
    line1 = Column(String, nullable=True)                # street / building
    line2 = Column(String, nullable=True)                # suite / floor
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    country = Column(String, nullable=True)
    pincode = Column(String, nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class DoctorContact(Base):
    """Email, phone, WhatsApp, fax. kind validated in the route layer."""
    __tablename__ = "doctor_contacts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)                # "email" | "phone" | "whatsapp" | "fax"
    label = Column(String, nullable=True)                # "Personal", "Reception", "Emergency"
    value = Column(String, nullable=False)               # the actual email/phone string
    is_primary = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class PatientDoctorLink(Base):
    __tablename__ = "patient_doctor_links"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # "Cardiology", "General", etc. — set from DoctorProfile.specialization at link time.
    department = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # A given patient can't be linked to the same doctor twice.
        UniqueConstraint("patient_id", "doctor_user_id", name="uq_patient_doctor_link"),
    )


class PatientDoctorRequest(Base):
    """A patient-initiated request to link a doctor. Exists only while the
    request is pending — accept / reject / cancel removes the row after
    notifying both sides. patient_doctor_links stays untouched (rows there
    are always active links), so doctor rosters keep working unchanged.
    The unique (patient, doctor) pair prevents duplicate pending requests.
    """
    __tablename__ = "patient_doctor_requests"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("patient_id", "doctor_user_id", name="uq_patient_doctor_request"),
    )


class PatientCareEvent(Base):
    """Audit/history of the patient's care-network relationship changes
    (doctors + hospitals). Append-only: every link/unlink/request decision
    writes one row so the patient's Doctors/Hospitals pages can show a
    timeline of "linked on … / unlinked on …" dates."""
    __tablename__ = "patient_care_events"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # doctor_link | doctor_unlink | doctor_request | doctor_request_accepted
    # | doctor_request_rejected | doctor_request_cancelled | hospital_link
    # | hospital_unlink
    kind = Column(String, nullable=False, index=True)
    # "doctor" | "hospital"
    target_kind = Column(String, nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class PatientHospitalLink(Base):
    """Patient ↔ Hospital relationship. Mirrors PatientDoctorLink but keeps
    history: unlinking flips `status` to "unlinked" and stamps `unlinked_at`
    instead of deleting the row, so a patient can re-link later and the
    relationship history stays auditable. Only one ACTIVE (status="linked")
    link per (patient, hospital) is allowed — enforced by the routes since
    SQLite has no partial unique index."""
    __tablename__ = "patient_hospital_links"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    # "linked" | "unlinked"
    status = Column(String, nullable=False, default="linked", index=True)
    linked_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    unlinked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # One row per (patient, hospital) ever. Unlink flips status to
        # "unlinked"; re-linking reactivates the same row, so duplicates
        # and double-active links are impossible at the DB level.
        UniqueConstraint("patient_id", "hospital_id", name="uq_patient_hospital_link"),
    )


# ---------- Shopkeeper: Inventory ----------
# One row per (medicine_name, batch_no) tuple. shopkeeper_id scopes everything
# to the owning user so multiple shops share the same DB without collision.

class MedicineStock(Base):
    __tablename__ = "medicine_stock"

    id = Column(Integer, primary_key=True, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    medicine_name = Column(String, nullable=False, index=True)
    # NEW (2026-09): active ingredients / composition text used by the
    # medicine-intelligence search and billing autocomplete. Nullable for
    # back-compat with older rows.
    composition = Column(String, nullable=True, index=True)
    batch_no = Column(String, nullable=False)
    manufacture_date = Column(Date, nullable=False)
    expiry_date = Column(Date, nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    unit_price = Column(Float, nullable=False, default=0.0)
    # NEW (2026-08): richer batch metadata. Nullable for back-compat with
    # older rows / clients. ``mrp`` is the listed retail price (separate
    # from unit_price which is what we charge); ``unit`` is one of
    # strip/piece/syrup/box/bottle (free-text so the shopkeeper can extend).
    manufacturer = Column(String, nullable=True)
    unit = Column(String, nullable=True)
    mrp = Column(Float, nullable=True)
    supplier = Column(String, nullable=True)
    low_stock_threshold = Column(Integer, nullable=False, default=10)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class Bill(Base):
    __tablename__ = "bills"
    __table_args__ = (
        # Bill numbers must be unique per shopkeeper, not globally — so two
        # shops can independently start numbering at INV-YYYYMMDD-0001.
        UniqueConstraint("shopkeeper_id", "bill_number", name="uq_bill_per_shop"),
    )

    id = Column(Integer, primary_key=True, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 2026-09: when a pharmacy bill is issued against a patient account, the
    # patient row is recorded here (authoritative identity link — never name/
    # phone matching). The patient then sees the bill in Manage → My Billings.
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True)
    bill_number = Column(String, nullable=False, index=True)
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=True)
    customer_address = Column(Text, nullable=True)
    subtotal = Column(Float, nullable=False, default=0.0)
    tax_percent = Column(Float, nullable=False, default=0.0)
    tax_amount = Column(Float, nullable=False, default=0.0)
    discount = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)
    payment_method = Column(String, nullable=True)        # "cash" | "upi" | "card" | "other"
    # NEW (2026-09): "paid" | "pending" | "cancelled". Nullable for
    # back-compat; new bills default to "paid".
    status = Column(String, nullable=True, index=True)
    notes = Column(Text, nullable=True)
    # NEW (2026-08): "inventory" bills decrement stock on save; "proforma"
    # bills don't. Default True so existing rows keep today's behavior and
    # new bills default to inventory unless the UI opts out.
    affects_inventory = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Note: no SQLAlchemy relationship here. We use explicit queries in the
    # endpoints (with selectinload when needed) so we never have to worry
    # about cascade miscounts — line items are inserted and queried directly
    # via the foreign key. Keeping it simple avoids the "items duplicated in
    # response" bug we hit during smoke testing.


class BillLineItem(Base):
    __tablename__ = "bill_line_items"

    id = Column(Integer, primary_key=True, index=True)
    bill_id = Column(Integer, ForeignKey("bills.id", ondelete="CASCADE"), nullable=False, index=True)
    medicine_name = Column(String, nullable=False)
    batch_no = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)
    # NEW (2026-08): richer per-line metadata so a printed/PDF bill can
    # show what was sold at full detail. All nullable for back-compat with
    # older line items.
    manufacture_date = Column(Date,    nullable=True)
    expiry_date      = Column(Date,    nullable=True)
    unit             = Column(String,  nullable=True)   # "strip"|"piece"|"syrup"|"box"|"bottle"
    mrp              = Column(Float,   nullable=True)   # listed retail price


# ---------- Shopkeeper: Activity / audit feed ----------
# One row per shopkeeper action (bill created, stock adjusted, medicine
# added, customer billed, ...). Feeds the dashboard's Recent Live Updates,
# the activity heatmap, and the per-role analytics. Deleting a row here
# only removes the activity/notification entry — never the underlying
# medical/billing/inventory data.

class ShopkeeperActivity(Base):
    __tablename__ = "shopkeeper_activities"

    id = Column(Integer, primary_key=True, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # action is a machine-readable slug ("bill_created", "stock_adjusted",
    # "medicine_added", "medicine_deleted", "customer_added", ...).
    action = Column(String, nullable=False, index=True)
    # Human-readable summary, e.g. "Bill INV-20260904-0001 generated".
    detail = Column(String, nullable=False)
    # Optional reference to the underlying row (bill id, stock id...).
    ref_type = Column(String, nullable=True)   # "bill" | "stock" | "customer"
    ref_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ---------- Shopkeeper: Expiry alert preferences ----------
# One row per shopkeeper; controls how many days before expiry they want to
# be warned. Default 30 days.

class ExpiryAlertPreference(Base):
    __tablename__ = "expiry_alert_preferences"

    id = Column(Integer, primary_key=True, index=True)
    shopkeeper_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    lead_days = Column(Integer, nullable=False, default=30)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    # Thread identity.
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Who actually sent this message. Doctors always send as the doctor;
    # patients always send as the patient. We still record sender_user_id
    # explicitly so future features (e.g. a "shared doctor inbox" with a
    # nurse sending on the doctor's behalf) have somewhere to point.
    sender_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    body = Column(Text, nullable=True)        # text content; null when only attachments are sent
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Read receipts — last time each side of the thread marked it read.
    doctor_last_read_at = Column(DateTime, nullable=True)
    patient_last_read_at = Column(DateTime, nullable=True)
    # --- Conversation interactions (2026-09) ------------------------------
    # Reply chain: this message is a reply to reply_to_id (same thread).
    reply_to_id = Column(Integer, nullable=True, index=True)
    # Message edited in place (UI shows an "edited" chip).
    edited_at = Column(DateTime, nullable=True)
    # Delete-for-everyone: soft delete — row stays for audit/retention but
    # body/attachments are hidden from the normal UI.
    deleted_at = Column(DateTime, nullable=True)
    deleted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Delete-for-me: JSON array of user ids that hid this message from their
    # own view. Kept on the row so every participant can hide independently.
    hidden_from_user_ids = Column(Text, nullable=True)  # JSON list of user ids
    # Client-generated idempotency key: when a retry re-sends the same key the
    # backend returns the already-stored message instead of duplicating it.
    client_message_id = Column(String, nullable=True, index=True)


class ChatMessageReaction(Base):
    """One reaction per (message, user). Adding again replaces the emoji;
    unique constraint prevents duplicate reaction rows from one user."""
    __tablename__ = "chat_reactions"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reaction = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_chat_reaction_msg_user"),)


class ChatConversationPref(Base):
    """Per-(user, thread-pair) conversation preferences + lightweight presence.

    One row per participant per thread. Backs pin / archive / mute (with
    expiry), the typing indicator, and read-cursor caching. Thread identity is
    the same (patient_id, doctor_user_id) pair the rest of chat uses.
    """
    __tablename__ = "chat_conversation_prefs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    pinned = Column(Integer, default=0, nullable=False)      # 0 | 1
    archived = Column(Integer, default=0, nullable=False)    # 0 | 1
    muted_until = Column(DateTime, nullable=True)            # NULL = not muted
    typing_until = Column(DateTime, nullable=True)           # set by typing pings
    online_until = Column(DateTime, nullable=True)           # presence heartbeat
    last_seen_at = Column(DateTime, nullable=True)           # last activity in this thread
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint(
        "user_id", "patient_id", "doctor_user_id",
        name="uq_chat_conv_pref_user_pair",
    ),)


class ChatPrivacy(Base):
    """Per-user chat privacy controls (2026-09).

    These gate what OTHER users are allowed to see about the account owner:
      * show_last_seen       -> partner online/offline + last-seen presence
      * show_read_receipts   -> whether the owner's reads are broadcast (the
                                sender of a message only sees the double
                                read-tick when the other side keeps this on)
      * show_typing          -> whether "typing…" is shown to the other side
    Rows are created lazily on the first PUT; reads fall back to all-enabled.
    """
    __tablename__ = "chat_privacy"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    show_last_seen = Column(Integer, default=1, nullable=False)     # 1 = others see online/last seen
    show_read_receipts = Column(Integer, default=1, nullable=False)  # 1 = others see when I read
    show_typing = Column(Integer, default=1, nullable=False)         # 1 = others see "typing…"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatSavedMessage(Base):
    """A message starred/saved by a user (their own Saved view)."""
    __tablename__ = "chat_saved_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "message_id", name="uq_chat_saved_user_msg"),)


class ChatAttachment(Base):
    __tablename__ = "chat_attachments"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name = Column(String, nullable=False)        # original filename, used for the download dialog
    stored_path = Column(String, nullable=False)      # e.g. "uploads/chat/<uuid>_name.jpg"
    mime_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    # "image" | "document" | "video" | "other" — derived from mime at upload
    # time so the UI doesn't have to re-derive on every render.
    kind = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



class ChatSettings(Base):
    """Per-thread settings: block/unblock, chat lock (PIN), theme."""
    __tablename__ = "chat_settings"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Who set the lock (user_id). NULL = no lock.
    locked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Bcrypt hash of the 4-digit PIN.
    pin_hash = Column(String, nullable=True)
    # Block state: NULL/0 = not blocked, 1 = blocked.
    is_blocked = Column(Integer, default=0, nullable=False)
    blocked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Chat theme: 'default' | 'dark' | 'ocean' | 'sunset' | 'forest' | 'lavender'
    theme = Column(String, default='default', nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------- Hospital: Wards / Beds / Admissions ----------
# A Ward is a physical room or section of the hospital (e.g. "ICU-A",
# "General Ward 1"). It carries a per-day billing rate so admissions can
# accrue charges automatically. A Bed belongs to exactly one Ward; an
# Admission pins a Patient to a Bed with admit/discharge timestamps and
# optional notes. BedTransfer is an audit log of moves between beds.

class Ward(Base):
    __tablename__ = "wards"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)             # "ICU-A", "General Ward 1"
    # Free-text for now so the UI can offer extra types later without a
    # migration. Common values: "general" | "icu" | "pediatric" |
    # "maternity" | "private" | "isolation".
    ward_type = Column(String, nullable=False, default="general")
    # ₹ per day, used by GET /admissions/{id}/charges. Default is the
    # cheapest common rate; doctor overrides per ward at creation time.
    daily_rate = Column(Float, nullable=False, default=500.0)
    # Capacity hint — UI uses this for "X of Y beds occupied" stat.
    # The actual bed count is `len(beds)`; this column is the planned
    # capacity which may briefly exceed until beds are added.
    total_beds = Column(Integer, nullable=False, default=10)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # NULL = unassigned / legacy ward. Set by a hospital admin when they
    # create a new ward from the hospital dashboard. The hospital-admin
    # place-patient flow only sees beds whose ward.hospital_id matches
    # their own hospital.
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)

    beds = relationship("Bed", back_populates="ward", cascade="all, delete-orphan")
    hospital = relationship("Hospital", back_populates="wards")


class Bed(Base):
    __tablename__ = "beds"

    id = Column(Integer, primary_key=True, index=True)
    ward_id = Column(Integer, ForeignKey("wards.id", ondelete="CASCADE"), nullable=False, index=True)
    # Free-text bed number ("A-01", "G-12"). Unique within a ward, but
    # the same bed number can exist in two different wards (e.g. ward A
    # and ward B both have a "1" bed) — hence the composite unique.
    bed_number = Column(String, nullable=False)
    # Free-text for now: "standard" | "ventilator" | "oxygen" | etc.
    bed_type = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    ward = relationship("Ward", back_populates="beds")
    # NOTE: we do NOT add a SQLAlchemy relationship from Bed → Admission
    # because a bed can only be occupied by one active admission at a
    # time and the endpoints query admissions directly via ForeignKey.
    # (Mirrors the Bill / BillLineItem pattern — see comment on Bill.)

    __table_args__ = (
        UniqueConstraint("ward_id", "bed_number", name="uq_bed_per_ward"),
    )


class Admission(Base):
    __tablename__ = "admissions"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    bed_id = Column(Integer, ForeignKey("beds.id"), nullable=False, index=True)
    # The doctor who created this admission — used for the notes-edit
    # permission guard ("only the admitting doctor can edit notes").
    admitted_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    admitted_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    # NULL = currently admitted. Set when discharged.
    discharged_at = Column(DateTime, nullable=True, index=True)
    discharged_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    discharge_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # Denormalized for stable billing: charges use the ward's daily_rate
    # at admission time, even if the ward's rate later changes. Avoids
    # needing a "rate history" table.
    ward_id_at_admit = Column(Integer, ForeignKey("wards.id"), nullable=False)
    # NULL for legacy doctor-direct admits. Set by the hospital admin's
    # place-patient flow. Lets the hospital scope their own admissions
    # list without joining beds/wards.
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)

    # No `relationship()` declarations — endpoints issue explicit
    # queries (same pattern as Bill / BillLineItem) so we never have to
    # reason about cascade miscounts on multi-table joins.


class BedTransfer(Base):
    __tablename__ = "bed_transfers"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    from_bed_id = Column(Integer, ForeignKey("beds.id"), nullable=False)
    to_bed_id = Column(Integer, ForeignKey("beds.id"), nullable=False)
    transferred_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    transferred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reason = Column(Text, nullable=True)


class AdmissionRequest(Base):
    """A patient-initiated request for a specific bed. A doctor must accept
    or reject it before any Admission row is created.

    Status flow:
        pending  -> accepted  (becomes an Admission row)
        pending  -> rejected  (terminal, with optional reason)
        pending  -> cancelled (by the patient, terminal)

    A request is uniquely scoped to (patient, bed) while pending so the
    patient can't spam duplicate requests for the same bed; once the row
    leaves "pending" the constraint no longer applies and the same
    (patient, bed) pair can be requested again in the future.
    """
    __tablename__ = "admission_requests"
    __table_args__ = (
        # Only one PENDING request per (patient, bed) at a time. The
        # partial UNIQUE index below narrows this to pending rows; older
        # SQLite without partial-index support will fall back to the
        # raw UNIQUE in _run_lightweight_migrations().
        UniqueConstraint("patient_id", "bed_id", "status", name="uq_admreq_patient_bed_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    bed_id = Column(Integer, ForeignKey("beds.id"), nullable=False, index=True)
    # The patient account that filed the request (User row, role=patient).
    # Used to enforce "only the requester can cancel their own request".
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    # "pending" | "accepted" | "rejected" | "cancelled"
    status = Column(String, nullable=False, default="pending", index=True)
    decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_reason = Column(Text, nullable=True)
    # The free-text reason the patient gave when filing. Optional.
    request_reason = Column(Text, nullable=True)
    # Once accepted, points at the resulting Admission row so the UI can
    # deep-link the request → admission pair.
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="SET NULL"), nullable=True)
    # "patient_to_doctor" (default — patient asking a doctor for a specific bed)
    # | "doctor_to_hospital" (NEW — doctor asking a hospital to admit a patient).
    # Older rows are NULL; routes treat NULL as the legacy patient_to_doctor flow.
    request_kind = Column(String, nullable=True, index=True)
    # When request_kind = "doctor_to_hospital", records which hospital the
    # doctor is asking. NULL for the legacy flow.
    target_hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)
    # The doctor who filed the request (User row, role=doctor). NULL for the
    # legacy patient-to-doctor flow.
    requested_by_doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # When the hospital admin places the patient, this back-link is set so
    # the doctor can navigate from their request directly to the resulting
    # Admission. Distinct from `admission_id` which only makes sense after
    # the legacy flow is complete.
    placed_admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="SET NULL"), nullable=True)


# ---------- Hospital, Hospital Admins, Nurse, Discharge Appeals ----------
# Doctor admits a patient to a hospital (no bed yet). Hospital admin picks
# a free bed/ward from their own hospital and assigns a nurse. Nurse
# manages the patient and files a discharge appeal that walks through
# hospital then doctor for final sign-off (3-step).

class Hospital(Base):
    """A physical hospital or clinic. The first hospital admin to sign up
    creates a row here; subsequent admins join by id (see HospitalProfile).
    A Hospital owns Wards and Beds through the wards.hospital_id FK."""
    __tablename__ = "hospitals"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    address = Column(Text, nullable=True)
    phone = Column(String, nullable=True)
    # The hospital admin who created this row.
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Backrefs. We do NOT cascade delete Wards/Beds on hospital delete —
    # a hospital with admitted patients must be archived, not dropped.
    wards = relationship("Ward", back_populates="hospital")


class HospitalProfile(Base):
    """One row per hospital-role User, pointing at the Hospital they admin.
    Nullable `hospital_id` means the user hasn't created/joined a hospital yet."""
    __tablename__ = "hospital_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)
    display_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NurseAssignment(Base):
    """One active row per (patient, nurse). Hospital admin can replace the
    active nurse by inserting a new active row; the route deactivates the
    old one. Mirrors the PatientDoctorLink pattern."""
    __tablename__ = "nurse_assignments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    nurse_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    assigned_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        # Only one ACTIVE assignment per (patient, nurse) pair. Inactive
        # rows are kept for audit.
        UniqueConstraint("patient_id", "nurse_user_id", "is_active", name="uq_nurse_assignment_patient_active"),
    )


class DischargeAppeal(Base):
    """3-step discharge workflow:
        nurse files (pending_hospital) →
        hospital admin approves (pending_doctor) →
        admitting doctor approves (approved) AND Admission is discharged.
    A single active appeal per admission is enforced by the route."""
    __tablename__ = "discharge_appeals"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    nurse_reason = Column(Text, nullable=True)

    # Step 1: hospital admin decision
    hospital_decision = Column(String, nullable=True)              # "approved" | "rejected"
    hospital_decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    hospital_decided_at = Column(DateTime, nullable=True)
    hospital_reason = Column(Text, nullable=True)

    # Step 2: doctor decision
    doctor_decision = Column(String, nullable=True)
    doctor_decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    doctor_decided_at = Column(DateTime, nullable=True)
    doctor_reason = Column(Text, nullable=True)

    # "pending_hospital" | "pending_doctor" | "approved" | "rejected" | "cancelled"
    status = Column(String, nullable=False, default="pending_hospital", index=True)


class Medication(Base):
    """A medication the nurse is administering to an admitted patient.
    Logged against the active admission (not the bare patient) so the
    history is tied to the hospital stay. Nurses create/update/discontinue
    entries; the doctor can view them on the patient dashboard."""
    __tablename__ = "medications"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    # Free-text drug name (e.g. "Amoxicillin") — no formulary check.
    name = Column(String, nullable=False)
    dosage = Column(String, nullable=True)        # e.g. "500mg"
    frequency = Column(String, nullable=True)     # e.g. "twice daily"
    route = Column(String, nullable=True)         # e.g. "oral", "IV"
    notes = Column(Text, nullable=True)
    # "active" | "discontinued" | "completed"
    status = Column(String, nullable=False, default="active")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    stopped_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Treatment(Base):
    """A treatment / procedure the nurse is performing on an admitted
    patient (e.g. wound dressing, physiotherapy). Like Medication, scoped
    to the active admission so history is per-stay."""
    __tablename__ = "treatments"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    name = Column(String, nullable=False)         # e.g. "Wound dressing"
    category = Column(String, nullable=True)      # e.g. "dressing", "physio"
    description = Column(Text, nullable=True)
    schedule = Column(String, nullable=True)      # e.g. "every 4h"
    notes = Column(Text, nullable=True)
    # "scheduled" | "in_progress" | "completed" | "cancelled"
    status = Column(String, nullable=False, default="scheduled")
    scheduled_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NurseNote(Base):
    """A free-text nurse observation / shift update. When `sent_to_doctor`
    is True a NotificationEvent is enqueued for the patient's admitting
    doctor so they see it in the bell feed and on the dashboard."""
    __tablename__ = "nurse_notes"

    id = Column(Integer, primary_key=True, index=True)
    admission_id = Column(Integer, ForeignKey("admissions.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=False)
    # Optional structured tags: "vitals", "observation", "incident", "other".
    category = Column(String, nullable=True)
    # When True, a NotificationEvent was enqueued for the doctor.
    sent_to_doctor = Column(Integer, nullable=False, default=0)
    # 2026-08: on-demand Gemini summary of the admission's medications +
    # treatments + this note. Filled in by create_nurse_note when
    # ``send_to_doctor=True`` so the doctor sees a short, readable summary
    # in their chat bubble. Nullable so old notes still render.
    summary = Column(Text, nullable=True)
    summary_generated_at = Column(DateTime, nullable=True)
    # 2026-08: "free_text" = nurse-typed report (default for legacy rows);
    # "report_aggregate" = AI-aggregated summary of the admission's
    # medications + treatments + prior notes. NULL is treated as
    # free_text by the renderer so the migration is purely additive.
    kind = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class NurseNoteAttachment(Base):
    """A file the nurse attached when filing a nurse-note (text + image /
    doc / video). Lives alongside the note and is exposed to the doctor's
    chat view when ``sent_to_doctor=True``."""
    __tablename__ = "nurse_note_attachments"

    id = Column(Integer, primary_key=True, index=True)
    note_id = Column(Integer, ForeignKey("nurse_notes.id", ondelete="CASCADE"), nullable=False, index=True)
    file_name = Column(String, nullable=False)
    stored_path = Column(String, nullable=False)
    mime_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    # "image" | "video" | "document" | "other" — derived from mime at upload.
    kind = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ===========================================================================
# CARE-TASK ASSIGNMENTS (2026-09)
# ---------------------------------------------------------------------------
# Centralized task-assignment workflow: a Doctor or Hospital user assigns a
# care task to a Nurse for a Patient. One table for every source (the
# assigning role is stored on the row) so there is no doctor-vs-hospital
# split. Status flow:
#   assigned -> acknowledged -> in_progress -> completed
#                    |               |-> on_hold -> in_progress (resume)
#                    |               `-> escalated
#                    `-> cancelled (assigner / hospital only)
# Every transition appends a NurseTaskEvent row (audit + history). "overdue"
# is computed from due_at and is never stored as a status.
# ===========================================================================

class NurseTask(Base):
    """A care task assigned to a nurse for a patient by a doctor or hospital."""
    __tablename__ = "nurse_tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    instructions = Column(Text, nullable=True)
    # The patient the task concerns.
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    # The nurse who received the task.
    nurse_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Which organization context the assignment was made in (nullable for
    # doctors who are not linked to a hospital).
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)
    # Creator identity + role: "doctor" | "hospital".
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_by_role = Column(String, nullable=False, default="doctor")
    # "low" | "medium" | "high" | "urgent"
    priority = Column(String, nullable=False, default="medium", index=True)
    # "assigned" | "acknowledged" | "in_progress" | "on_hold" |
    # "completed" | "cancelled" | "escalated"
    status = Column(String, nullable=False, default="assigned", index=True)
    department = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=True, index=True)
    # 0-100. The nurse updates this as they work.
    progress = Column(Integer, nullable=False, default=0)

    acknowledged_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(Text, nullable=True)

    escalated_at = Column(DateTime, nullable=True)
    escalated_to_role = Column(String, nullable=True)  # "doctor" | "hospital"
    escalation_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Nurse dashboard queries: "my open tasks", "my due tasks".
        Index("ix_nurse_tasks_nurse_status", "nurse_user_id", "status"),
        Index("ix_nurse_tasks_nurse_due", "nurse_user_id", "due_at"),
    )


class NurseTaskEvent(Base):
    """Append-only history for a care-task assignment. Also stores nurse
    notes (kind='note') and follow-up scheduling (kind='followup_scheduled')
    so every longitudinal record lives in one table.
    Kinds: created | acknowledged | started | progress | note | paused |
    resumed | escalated | completed | cancelled | followup_scheduled |
    followup_completed
    """
    __tablename__ = "nurse_task_events"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("nurse_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)
    # The user who performed the action (nurse, assigner, or hospital staff).
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    note = Column(Text, nullable=True)
    # Snapshot of the task's progress % at the time of the event.
    progress = Column(Integer, nullable=True)
    # For kind='followup_scheduled' this is the requested follow-up time.
    due_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ===========================================================================
# SYSTEM ACTIVITY ENGINE (2026-09)
# ---------------------------------------------------------------------------
# One append-only table for role-appropriate "what happened lately" feeds and
# the activity heatmaps on every dashboard. Hooks are fired from real
# endpoints (appointments, care-task assignments, nurse notes, ...) — never
# from fake/demo data. Stored detail text is deliberately kept free of
# private medical content; scope ids (patient / hospital / nurse) drive
# role-based visibility.
# ===========================================================================

class SystemActivity(Base):
    __tablename__ = "system_activities"

    id = Column(Integer, primary_key=True, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    actor_role = Column(String, nullable=True, index=True)
    # Machine slug e.g. "task_assigned", "appointment_booked",
    # "appointment_status", "nurse_note_added", ...
    event_type = Column(String, nullable=False, index=True)
    # "nurse_task" | "appointment" | "nurse_note" | ...
    entity_type = Column(String, nullable=True)
    entity_id = Column(Integer, nullable=True)
    # Human-readable, non-medical summary.
    detail = Column(String(300), nullable=True)
    # Scope pointers for role-based visibility.
    patient_id = Column(Integer, nullable=True, index=True)
    hospital_id = Column(Integer, nullable=True, index=True)
    nurse_user_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ===========================================================================
# ROLE-BASED SETTINGS (2026-09)
# ---------------------------------------------------------------------------
# Per-user settings stored as a JSON blob keyed by setting name. The allowed
# keys + types live in the backend catalog (main._ROLE_SETTINGS) so the
# frontend can never invent a setting and invalid values are rejected
# server-side. One row per user, created lazily with role defaults.
# ===========================================================================

class UserSetting(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    # JSON object {key: value, ...}.
    data = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# PATIENT BILLING + PAYMENT LEDGER (2026-09)
# ---------------------------------------------------------------------------
# One unified payment ledger across both bill sources. A bill is a read-model
# over the real rows (shopkeeper `bills` / hospital `hospital_invoices`) — we
# never duplicate bill content. Payments are the only thing created here and
# they are strictly backend-authoritative: amounts are recomputed from the
# originating bill, status never comes from the frontend, and every state
# change is appended to BillingPaymentEvent.
#
# Payment lifecycle:
#   initiated -> processing -> submitted -> awaiting_approval (hospital
#   bills) OR approved (pharmacy/auto) -> approved -> (refund_requested ->
#   refunded). A patient can cancel while initiated/awaiting_approval. A
#   hospital user approves/rejects/refunds only for its own hospital bills.
# ===========================================================================

class BillingPayment(Base):
    __tablename__ = "billing_payments"

    id = Column(Integer, primary_key=True, index=True)
    # "hospital" | "pharmacy"
    bill_source = Column(String, nullable=False, index=True)
    # Row id in hospital_invoices (bill_source="hospital") or bills
    # (bill_source="pharmacy").
    bill_id = Column(Integer, nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=True, index=True)
    # Snapshot of the bill reference + totals at payment time (audit copy;
    # amount is still re-validated against the live bill before each step).
    bill_number = Column(String, nullable=True)
    bill_total = Column(Float, nullable=False, default=0.0)
    amount = Column(Float, nullable=False)
    outstanding_before = Column(Float, nullable=False, default=0.0)
    # "bank" | "card" | "upi" — never raw credentials, only a masked
    # reference (e.g. "upi/****abc@okhdfc" or "card/****1234") provided by
    # the gateway layer.
    method = Column(String, nullable=False)
    method_detail = Column(String, nullable=True)
    transaction_reference = Column(String, nullable=False, unique=True, index=True)
    # Client-supplied key so double-submits resolve to the same transaction.
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    # "initiated" | "processing" | "submitted" | "awaiting_approval" |
    # "approved" | "rejected" | "failed" | "cancelled" |
    # "refund_requested" | "refunded"
    status = Column(String, nullable=False, default="initiated", index=True)
    # Hospital bills require hospital approval before they count as paid.
    needs_approval = Column(Boolean, nullable=False, default=False)
    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    refund_reason = Column(Text, nullable=True)
    refunded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Fast patient billing + hospital approval lists.
        Index("ix_billing_payments_patient", "patient_id", "created_at"),
        Index("ix_billing_payments_hospital", "hospital_id", "status"),
        Index("ix_billing_payments_bill", "bill_source", "bill_id"),
    )


class BillingPaymentEvent(Base):
    """Append-only financial ledger for every payment state change."""
    __tablename__ = "billing_payment_events"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("billing_payments.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Who acted: "patient" | "system" | "hospital" | "gateway"
    actor_kind = Column(String, nullable=False, default="system")
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SettingAudit(Base):
    """Audit trail for settings changes: user, role, key, old value, new
    value, timestamp. No sensitive values beyond the setting itself."""
    __tablename__ = "setting_audits"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String, nullable=True)
    key = Column(String, nullable=False, index=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ---------- Nurse profile (rich data on top of User) ----------
# Mirrors DoctorProfile so hospital admins can register nurses with the same
# depth of detail as doctors. The User row carries the login identity;
# this table carries the bio/department/experience data.
class NurseProfile(Base):
    __tablename__ = "nurse_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    department = Column(String, nullable=True)            # e.g. "ICU", "Pediatrics", "General Ward"
    qualifications = Column(Text, nullable=True)          # e.g. "B.Sc Nursing, GNM"
    experience_years = Column(Integer, nullable=True)
    current_address = Column(Text, nullable=True)
    permanent_address = Column(Text, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    country = Column(String, nullable=True)
    pincode = Column(String, nullable=True)
    languages = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    shift_preference = Column(String, nullable=True)      # "morning" | "evening" | "night" | "rotating"
    registration_number = Column(String, nullable=True)   # nursing council reg no
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class HospitalRegistrationLink(Base):
    __tablename__ = "hospital_registration_links"
    __table_args__ = (
        UniqueConstraint("hospital_id", "user_id", "role", name="uq_hosp_reg_link"),
    )

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # "doctor" | "patient" | "nurse"
    role = Column(String, nullable=False)
    registered_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- Appointments ----------
# Doctor <-> patient scheduled visits. Booked either by a hospital admin
# (for the appointment tab) or by a patient (self-book flow). Both flows
# land in the same table so the doctor dashboard, patient dashboard, and
# hospital admin's Appointments tab all render from one source.
class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    scheduled_at = Column(DateTime, nullable=False, index=True)
    duration_minutes = Column(Integer, default=30, nullable=False)
    room_number = Column(String, nullable=True)
    department = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # "scheduled" | "confirmed" | "completed" | "cancelled" | "no_show"
    status = Column(String, default="scheduled", nullable=False)
    # New fields for enhanced appointment management
    appointment_type = Column(String, default="consultation", nullable=True)  # consultation|follow_up|emergency|surgery|lab|imaging
    priority = Column(String, default="normal", nullable=True)  # normal|high|urgent
    confirmed_at = Column(DateTime, nullable=True)
    rescheduled_from = Column(Integer, nullable=True)  # original appointment ID if rescheduled
    # Patient feedback when completing an appointment
    feedback = Column(Text, nullable=True)
    feedback_rating = Column(Integer, nullable=True)  # 1-5 stars
    feedback_at = Column(DateTime, nullable=True)
    # True when the patient (rather than an admin) booked this appointment.
    booked_by_patient = Column(Boolean, default=False, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class MediEchoSession(Base):
    __tablename__ = "mediecho_sessions"

    id = Column(Integer, primary_key=True, index=True)
    # The doctor who captured / uploaded this conversation. Nullable so
    # patient accounts (who are uploading their own recordings for their
    # own record) can create a session without being linked to a doctor.
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Optional patient link. Sessions can be saved without a patient
    # attached (the doctor is just triaging). When linked, the saved
    # note flows into that patient's chart.
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    # Free-text title shown in the doctor's session list.
    title = Column(String, nullable=True)
    # Original upload filename (if any) — kept for the UI download link.
    audio_filename = Column(String, nullable=True)
    # Where the audio file lives on disk under UPLOAD_DIR. NULL when the
    # doctor recorded via mic and we haven't persisted the blob yet, or
    # when the doctor deleted the audio after saving the note.
    audio_stored_path = Column(String, nullable=True)
    mime_type = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    # Wall-clock duration in seconds (from the STT engine). 0 if unknown.
    duration_seconds = Column(Float, nullable=True, default=0.0)
    # STT engine identifier (whisper model name) for audit.
    stt_model = Column(String, nullable=True)
    # Whisper segments: JSON-encoded list of {start, end, speaker, text}.
    transcript_json = Column(Text, nullable=True)
    # Lifecycle:
    #   "recording"  — mic open or upload in progress
    #   "transcribing" — STT running
    #   "transcribed" — STT done, note not yet generated
    #   "analyzing"  — Gemini extracting the note
    #   "ready"      — note is editable
    #   "saved"      — note approved + linked to a MedicalRecord
    #   "failed"     — terminal error (see error_message)
    status = Column(String, nullable=False, default="recording", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class MediEchoNote(Base):
    __tablename__ = "mediecho_notes"

    id = Column(Integer, primary_key=True, index=True)
    # One-to-one with the session. CASCADE on delete so removing the
    # session cleans up its note (and avoids orphan notes).
    session_id = Column(Integer, ForeignKey("mediecho_sessions.id", ondelete="CASCADE"),
                        nullable=False, unique=True, index=True)
    # AI-extracted structured fields as JSON (symptoms, history, etc.).
    extracted_json = Column(Text, nullable=True)
    # Plain-language clinical summary (the doctor can edit this).
    summary = Column(Text, nullable=True)
    # Highlights as JSON list of {kind, quote, why}.
    highlights_json = Column(Text, nullable=True)
    # Doctor-edited overrides. Stored as JSON so the doctor can tweak
    # any field of the extracted note without losing the AI's first
    # pass (saved to extracted_json).
    edited_json = Column(Text, nullable=True)
    # "draft" | "approved" | "saved"
    status = Column(String, nullable=False, default="draft", index=True)
    approved_at = Column(DateTime, nullable=True)
    saved_at = Column(DateTime, nullable=True)
    # Once the doctor promotes the note to a real chart entry, this points
    # at the MedicalRecord id that was created.
    saved_to_record_id = Column(Integer, ForeignKey("medical_records.id", ondelete="SET NULL"),
                                nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


# ===========================================================================
# OPD MANAGEMENT (Outpatient Department)
# ===========================================================================
class AIDoctorSession(Base):
    __tablename__ = "ai_doctor_sessions"

    id = Column(Integer, primary_key=True, index=True)
    # The patient this session belongs to. Nullable so we can create
    # the row before flushing the patient association, but always set
    # before commit.
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"),
                        nullable=True, index=True)
    # The user (patient account) who opened the session. Lets us
    # audit "who typed what" without joining through patient_id.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # Free-text title — auto-generated from the first user message
    # (truncated to 80 chars) but the patient can rename it later.
    title = Column(String, nullable=True)
    # "active" | "closed". Closed sessions are read-only — the AI
    # reply pipeline refuses to append to a closed session.
    status = Column(String, nullable=False, default="active", index=True)
    # Plain-language medical summary generated on demand. NULL until
    # the patient asks for a summary or the server auto-generates one
    # on close.
    summary = Column(Text, nullable=True)
    # Cached age/gender/known_conditions snapshot that we send to
    # Gemini as system context. Filled at session-create time so the
    # demographic profile is stable for the lifetime of the session.
    patient_context_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_active_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIDoctorMessage(Base):
    __tablename__ = "ai_doctor_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("ai_doctor_sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    # "user" | "assistant" | "system"
    role = Column(String, nullable=False, index=True)
    # The plain-text body of the message. For assistant replies this
    # is the full Gemini response text (no streaming on the server —
    # the frontend renders typing dots while it waits).
    content = Column(Text, nullable=True)
    # Optional attached media (image/PDF). Stored on disk under
    # UPLOAD_DIR and referenced by relative path. The frontend reads
    # it via /uploads/ via the existing static handler.
    media_filename = Column(String, nullable=True)
    media_stored_path = Column(String, nullable=True)
    media_mime = Column(String, nullable=True)
    media_size = Column(Integer, nullable=True)
    # For assistant messages: "ok" | "blocked" | "error".
    # blocked = safety filter triggered, error = API failure.
    status = Column(String, nullable=True)
    # Latency (ms) for assistant turns, useful for the UI to show
    # "responded in 2.3s". 0 for user turns.
    latency_ms = Column(Integer, nullable=True, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIDoctorSettings(Base):
    """Per-account persona settings for the AI Doctor. One row per user,
    created lazily on first read with the default 'Dr. Mira' persona.
    The patient picks their own doctor name once and it sticks across
    every session — the only 'personalization' we allow (no avatars or
    custom system prompts because that would erode the clinical safety
    preamble)."""
    __tablename__ = "ai_doctor_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    persona_name = Column(String(40), nullable=False, default="Dr. Mira")
    # Reserved for a future 'pick an avatar' feature. Nullable so we
    # don't break the row shape on older DBs.
    persona_avatar_url = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)



class AdmissionQueue(Base):
    """Hospital admission queue — tracks every patient waiting for or
    currently going through the admission process.

    Status flow:
        waiting -> called -> in_process -> admitted
        waiting -> called -> cancelled
        waiting -> on_hold -> waiting (resume)
        waiting -> cancelled
    """
    __tablename__ = "admission_queue"

    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, ForeignKey("hospitals.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # referring doctor
    department = Column(String, nullable=True)   # "Cardiology", "General", "ICU", etc.
    admission_type = Column(String, nullable=False, default="normal")  # normal | emergency | surgery
    priority = Column(String, nullable=False, default="normal")  # normal | high | emergency
    status = Column(String, nullable=False, default="waiting", index=True)  # waiting|called|in_process|admitted|completed|cancelled|on_hold
    queue_number = Column(Integer, nullable=False, index=True)  # sequential queue number for the hospital
    reason = Column(Text, nullable=True)  # reason for admission
    notes = Column(Text, nullable=True)
    waiting_since = Column(DateTime, nullable=False, default=datetime.utcnow)
    called_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)  # when in_process started
    admitted_at = Column(DateTime, nullable=True)  # when fully admitted to a bed
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    on_hold_since = Column(DateTime, nullable=True)
    transferred_department = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# Evidence Cache (for Medical Knowledge Service)
# ===========================================================================
class EvidenceCache(Base):
    """Cache for external medical knowledge API results.

    Stores responses from MedlinePlus, RxNorm, DailyMed, openFDA
    to avoid redundant API calls. Entries have TTL-based expiry
    controlled by the service layer.
    """
    __tablename__ = "evidence_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(64), nullable=False, index=True)  # SHA256 hash
    source = Column(String(50), nullable=False, index=True)     # medlineplus, rxnorm, etc.
    query = Column(String(500), nullable=False)                 # original search query
    data_json = Column(Text, nullable=False)                    # JSON-serialized result
    created_at = Column(DateTime, default=datetime.utcnow)


class CallSession(Base):
    """Persistent call history for voice/video calls.

    Stores metadata about every call attempt. Raw audio/video is never
    stored. The in-memory _call_sessions dict handles live signaling;
    this table provides durable history and audit.
    """
    __tablename__ = "call_sessions"

    id = Column(Integer, primary_key=True, index=True)
    call_id = Column(String(32), nullable=False, unique=True, index=True)
    caller_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    callee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    conversation_id = Column(Integer, nullable=True)  # optional link to chat thread
    call_type = Column(String(10), nullable=False, default="voice")  # voice | video
    status = Column(String(20), nullable=False, default="ringing", index=True)
    # ringing | accepted | connected | ended | rejected | missed | cancelled | failed | timeout
    ended_reason = Column(String(50), nullable=True)  # caller_hung_up | callee_rejected | timeout | network_error | etc.
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    accepted_at = Column(DateTime, nullable=True)
    connected_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
