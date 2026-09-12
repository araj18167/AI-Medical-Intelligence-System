"""
Pydantic schemas.
These define what data looks like coming IN (requests) and going OUT (responses).
They are separate from models.py (which defines the actual database tables).
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime, date


# ---------- Auth / Users ----------

class UserCreate(BaseModel):
    username: str
    email: str
    full_name: Optional[str] = None
    password: str
    role: str = "patient"          # "patient", "doctor", or "nurse"
    patient_id: Optional[int] = None  # link to a Patient record, if role is patient
    # Optional. If role=="patient", this becomes the FIRST PatientDoctorLink
    # for the new user — their data will appear on that doctor's dashboard.
    # Set from the signup dropdown populated by GET /doctors.
    linked_doctor_id: Optional[int] = None


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    role: str
    patient_id: Optional[int] = None
    # Set only when a doctor flow generated this account via POST /patients.
    created_by_doctor_id: Optional[int] = None
    # Doctors this user (if patient) is currently linked to. Empty for
    # non-patient roles. The full set of links lives in patient_doctor_links;
    # this is denormalized here so the dashboard can render the avatar /
    # greeting without a second roundtrip.
    linked_doctors: list["PatientDoctorLinkOut"] = []
    profile_picture: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    profile_picture: Optional[str] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- Disease Prediction ----------

class PredictionRequest(BaseModel):
    patient_id: Optional[int] = None   # if provided, result is saved to this patient's records
    symptoms: str
    age: Optional[int] = None
    gender: Optional[str] = None
    history: Optional[str] = None


class PredictionOut(BaseModel):
    model_config = {"extra": "allow"}
    # Legacy fields (kept for backward compat)
    likely_conditions: list[str] = []
    risk_level: str = "medium"
    recommendation: str = ""
    see_doctor: bool = True
    affected_regions: list[str] = []
    # New detailed fields
    patient_summary: str = ""
    conditions: list[dict] = []
    severity: dict = {}
    red_flags: list[dict] = []
    medication_options: list[dict] = []
    safety_warnings: list[str] = []
    recommended_tests: list[dict] = []
    self_care: list[dict] = []
    when_to_see_doctor: list[str] = []
    questions_for_doctor: list[str] = []
    doctor_urgency: str = "within_48_hours"
    ai_confidence: dict = {}
    doctor_verification_required: bool = True
    clinical_disclaimer: str = ""


# ---------- Medicine Info ----------

class MedicineInfoRequest(BaseModel):
    medicine_name: str


class RelatedMedicine(BaseModel):
    name: str                                   # brand name OR composition (salt) name
    type: str                                   # "brand" | "composition"
    composition: Optional[str] = None           # active ingredient(s) string for brands
    note: str                                   # one-sentence relationship description


class MedicineInfoOut(BaseModel):
    medicine_name: str
    common_uses: list[str]
    typical_dosage: str
    side_effects: list[str]
    interactions: list[str]
    precautions: str
    related: list[RelatedMedicine] = []


# ---------- Diet & Fitness Plan ----------

class DietPlanRequest(BaseModel):
    age: int
    gender: str
    weight_kg: float
    height_cm: float
    conditions: Optional[str] = None
    fitness_goal: Optional[str] = None


class DietPlanOut(BaseModel):
    bmi: float
    bmi_category: str
    diet_recommendations: list[str]
    fitness_recommendations: list[str]
    calorie_target: str
    precautions: str


# ---------- Prescription Digitization ----------

class ExtractedMedicine(BaseModel):
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None


class PrescriptionDigitizeOut(BaseModel):
    doctor_name: Optional[str] = None
    medicines: list[ExtractedMedicine]
    notes: Optional[str] = None


# ---------- Vitals & Health Trends ----------

class VitalCreate(BaseModel):
    patient_id: int
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    blood_sugar: Optional[float] = None
    weight_kg: Optional[float] = None


class VitalOut(VitalCreate):
    id: int
    recorded_at: datetime

    class Config:
        from_attributes = True


class MetricTrend(BaseModel):
    values: list[float]
    latest: Optional[float] = None
    trend: str


class HealthTrendsOut(BaseModel):
    patient_id: int
    total_records: int
    weight_trend: MetricTrend
    blood_sugar_trend: MetricTrend
    blood_pressure_trend: MetricTrend


# ---------- Medicine Reminders ----------

class ReminderCreate(BaseModel):
    patient_id: int
    medicine_name: str
    dosage: Optional[str] = None
    times: str   # comma-separated, e.g. "08:00,14:00,20:00"


class ReminderOut(ReminderCreate):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class DueReminder(BaseModel):
    reminder_id: int
    medicine_name: str
    dosage: Optional[str] = None
    scheduled_time: str


class DueRemindersOut(BaseModel):
    patient_id: int
    current_time: str
    due_reminders: list[DueReminder]


# ---------- Patient ----------

class PatientCreate(BaseModel):
    name: str
    # age/gender are nullable in the DB (auto-created Patient rows from
    # patient signups have them null until the form is filled in), so the
    # input shape accepts them as Optional too.
    age: Optional[int] = None
    gender: Optional[str] = None
    contact: Optional[str] = None
    # PatientRecords (2026-08). Both nullable; the "search by Aadhaar /
    # place" filters rely on these being populated.
    aadhar_number: Optional[str] = None
    place: Optional[str] = None


class PatientOut(PatientCreate):
    id: int
    created_at: datetime
    # DEPRECATED — kept for one release so older clients keep parsing. New
    # code should read linked_doctors instead.
    onboarded_by_doctor_id: Optional[int] = None
    # The full set of M:N links for this patient. Populated by every endpoint
    # that returns a Patient so consumers never need a second roundtrip.
    linked_doctors: list["PatientDoctorLinkOut"] = []

    class Config:
        from_attributes = True  # allows returning SQLAlchemy objects directly


# ---------- Doctor → patient auto-credentials ----------

class PatientCreateByDoctor(PatientCreate):
    """Same shape as PatientCreate, plus a flag the doctor can flip to suppress
    the auto-generated login (e.g. for record-only entries)."""
    auto_create_login: bool = True
    # Optional department/reason for the FIRST link. If omitted, the doctor's
    # DoctorProfile.specialization is snapshotted in.
    department: Optional[str] = None
    # NEW (2026-08): optional "create + admit in one step". When ``bed_id``
    # is provided the create-patient endpoint also inserts an Admission row
    # in the same transaction. ``initial_notes`` is stored on that admission.
    bed_id: Optional[int] = None
    initial_notes: Optional[str] = None


class PatientCredentials(BaseModel):
    """Returned alongside PatientOut ONLY when the system just generated a
    login. Plain password is included — treat as one-time-display on the client."""
    user_id: int
    username: str
    email: str          # synthetic @patients.system.local address; patient can sign in with it
    password: str


class SetCredentials(BaseModel):
    """Body for POST /patients/{id}/set-credentials. Exactly one of
    ``new_password`` or ``auto_generate=True`` should be supplied.

    If neither is given, the server defaults to auto-generating. Passing
    both is allowed but ``new_password`` wins (so the doctor's typed value
    is never silently discarded)."""
    new_password: Optional[str] = None
    auto_generate: bool = False


class PatientWithCredentialsOut(PatientOut):
    auto_credentials: Optional[PatientCredentials] = None


# ---------- Medical Record ----------

class RecordCreate(BaseModel):
    patient_id: int
    symptoms: str
    diagnosis: Optional[str] = None
    notes: Optional[str] = None


class RecordOut(RecordCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Prescription ----------

class PrescriptionCreate(BaseModel):
    patient_id: int
    medicine_name: str
    dosage: Optional[str] = None
    instructions: Optional[str] = None


class PrescriptionOut(PrescriptionCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Doctor Dashboard ----------
# (defined last since it references PatientOut, RecordOut, PrescriptionOut, etc above)

class DoctorSummaryOut(BaseModel):
    patient: PatientOut
    records: list[RecordOut]
    prescriptions: list[PrescriptionOut]
    health_trends: Optional[HealthTrendsOut] = None
    active_reminders: list[ReminderOut]
    patient_profile: Optional["PatientProfileOut"] = None
    medical_history: list["MedicalHistoryOut"] = []
    reports: list["PatientReportOut"] = []
    ai_summary: str


# ---------- Doctor Professional Profile ----------

class DoctorProfileCreate(BaseModel):
    specialization: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    clinic_name: Optional[str] = None
    clinic_address: Optional[str] = None
    consultation_fee: Optional[str] = None
    bio: Optional[str] = None
    languages: Optional[str] = None
    available_hours: Optional[str] = None


class DoctorProfileOut(DoctorProfileCreate):
    id: int
    user_id: int
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------- Patient Profile (personal + habits) ----------

class PatientProfileCreate(BaseModel):
    date_of_birth: Optional[datetime] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    occupation: Optional[str] = None
    marital_status: Optional[str] = None
    smoking: Optional[str] = None
    alcohol: Optional[str] = None
    diet: Optional[str] = None
    exercise: Optional[str] = None
    sleep_hours: Optional[str] = None


class PatientProfileOut(PatientProfileCreate):
    id: int
    patient_id: int
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------- Medical History Entry ----------

class MedicalHistoryCreate(BaseModel):
    category: str                # condition | allergy | surgery | medication | family_history | immunization | other
    title: str
    details: Optional[str] = None
    diagnosed_year: Optional[int] = None
    is_ongoing: Optional[bool] = False


class MedicalHistoryOut(MedicalHistoryCreate):
    id: int
    patient_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Patient Report (uploaded file metadata) ----------

class PatientReportOut(BaseModel):
    id: int
    patient_id: int
    title: str
    category: Optional[str] = None
    file_name: str
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    uploaded_by_user_id: Optional[int] = None
    uploaded_at: datetime
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# Forward references for DoctorSummaryOut
# (and for UserOut / PatientOut → PatientDoctorLinkOut) are all rebuilt at
# the bottom of the file, after every referenced class is defined.


# ---------- MediScan (unified document analyzer) ----------

# Allowed document categories for the doc_type form field
MEDISCAN_DOC_TYPES = {"prescription", "lab_report", "imaging"}


class ExtractedMedicine(BaseModel):
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None


class ExtractedLabTest(BaseModel):
    name: str
    value: Optional[str] = None           # raw value as a string (number or text)
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    flag: Optional[str] = None            # "low" | "normal" | "high" | "unknown"


# We keep `extracted_data` as a plain dict (Pydantic-friendly) because its shape
# varies by doc_type. The frontend dispatches on `doc_type` to render it.
class MediScanResultOut(BaseModel):
    model_config = {"extra": "allow"}
    doc_type: str
    extracted_data: dict
    summary: str
    warnings: list[str] = []
    saved_scan_id: Optional[int] = None
    file_name: str
    # Enhanced analysis fields
    confidence: float = 0.0
    critical_values: list[str] = []
    urgency: str = "routine"  # routine | follow_up_soon | urgent | emergency
    pipeline_stages: list[dict] = []  # Pipeline visualization stages
    # Medication support
    medication_options: list[dict] = []
    safety_warnings: list[str] = []
    doctor_verification_required: bool = True
    clinical_disclaimer: str = ""


class DocumentScanOut(BaseModel):
    id: int
    patient_id: Optional[int] = None
    uploaded_by_user_id: Optional[int] = None
    doc_type: str
    file_name: str
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    extracted_json: str
    summary: Optional[str] = None
    warnings: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Notification preferences ----------

class NotificationPrefsOut(BaseModel):
    email_enabled: bool
    browser_enabled: bool
    reminder_lead_minutes: int
    # Per-feature opt-out flags for the real-time bell feed.
    notif_patient_added: bool
    notif_doctor_linked: bool
    notif_chat_message: bool
    updated_at: datetime

    class Config:
        from_attributes = True


class NotificationPrefsUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    browser_enabled: Optional[bool] = None
    reminder_lead_minutes: Optional[int] = None
    notif_patient_added: Optional[bool] = None
    notif_doctor_linked: Optional[bool] = None
    notif_chat_message: Optional[bool] = None


class RecentNotificationItem(BaseModel):
    """One row in the dashboard bell dropdown. Keep the shape flat so the
    frontend can render it without any extra lookups."""
    id: Optional[str] = None  # "evt-{id}" for events, otherwise None
    kind: str            # "reminder" | "patient_summary" | "system" | "patient_added" | "doctor_linked" | "chat_message"
    title: str
    detail: str
    when: datetime
    link: Optional[str] = None
    unread: bool = False


class NotificationEventOut(BaseModel):
    id: int
    kind: str
    title: str
    detail: Optional[str] = None
    link: Optional[str] = None
    created_at: datetime
    read_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RecentNotificationsOut(BaseModel):
    items: list[RecentNotificationItem]


class UnreadCountOut(BaseModel):
    count: int


# ---------- Doctor extended profile ----------
# In/Out shape per child table. Create payloads only require the minimum;
# everything else is optional. Out payloads include server-managed fields
# (id, created_at, updated_at) so the frontend can render fresh rows.

class WorkHistoryCreate(BaseModel):
    role: str
    organization: str
    location: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    description: Optional[str] = None


class WorkHistoryOut(WorkHistoryCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EducationCreate(BaseModel):
    degree: str
    institution: str
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    description: Optional[str] = None


class EducationOut(EducationCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AddressCreate(BaseModel):
    label: Optional[str] = None
    line1: Optional[str] = None
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    is_primary: bool = False


class AddressOut(AddressCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Allowed kinds for DoctorContact.kind — kept here so both the validator
# (used in main.py) and any future UI builder share the same source.
ALLOWED_CONTACT_KINDS = {"email", "phone", "whatsapp", "fax"}


class ContactCreate(BaseModel):
    kind: str
    label: Optional[str] = None
    value: str
    is_primary: bool = False


class ContactOut(ContactCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# Shopkeeper: Inventory, Expiry Alerts, Billing
# ============================================================

# Allowed lead-time values for expiry alerts. Centralized here so both the
# router and the frontend share the same source of truth.
ALLOWED_EXPIRY_LEAD_DAYS = {7, 15, 30, 60, 90, 180}


class MedicineStockCreate(BaseModel):
    medicine_name: str
    # NEW (2026-09): active ingredients / composition text.
    composition: Optional[str] = None
    batch_no: str
    manufacture_date: date
    expiry_date: date
    quantity: int = 0
    unit_price: float = 0.0
    # Optional richer batch metadata (2026-08). Sending without these is fine
    # — older clients and partial updates leave them null on the row.
    manufacturer: Optional[str] = None
    unit: Optional[str] = None
    mrp: Optional[float] = None
    supplier: Optional[str] = None
    low_stock_threshold: int = 10
    notes: Optional[str] = None


class MedicineStockUpdate(BaseModel):
    medicine_name: Optional[str] = None
    composition: Optional[str] = None
    batch_no: Optional[str] = None
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    quantity: Optional[int] = None
    unit_price: Optional[float] = None
    manufacturer: Optional[str] = None
    unit: Optional[str] = None
    mrp: Optional[float] = None
    supplier: Optional[str] = None
    low_stock_threshold: Optional[int] = None
    notes: Optional[str] = None


class MedicineStockOut(MedicineStockCreate):
    id: int
    shopkeeper_id: int
    created_at: datetime
    updated_at: datetime
    # Computed server-side: days_to_expiry, flags.
    days_to_expiry: int = 0
    is_expired: bool = False
    is_expiring_soon: bool = False
    is_low_stock: bool = False

    class Config:
        from_attributes = True


class ExpiryAlertPrefsOut(BaseModel):
    lead_days: int
    updated_at: datetime

    class Config:
        from_attributes = True


class ExpiryAlertPrefsUpdate(BaseModel):
    lead_days: int


class ExpiryDueRow(BaseModel):
    """Compact row for the expiry-due dashboard. Same shape as MedicineStockOut
    but includes the lead-time that matched the row."""
    id: int
    medicine_name: str
    batch_no: str
    manufacture_date: date
    expiry_date: date
    quantity: int
    supplier: Optional[str] = None
    days_to_expiry: int
    is_expired: bool
    is_expiring_soon: bool

    class Config:
        from_attributes = True


class ExpiryDueOut(BaseModel):
    lead_days: int
    items: list[ExpiryDueRow]


# ---------- Billing ----------

class BillLineItemIn(BaseModel):
    medicine_name: str
    batch_no: Optional[str] = None
    quantity: int
    unit_price: float
    # NEW (2026-08): per-line detail captured at billing time so a printed
    # bill/PDF can show the batch and unit. All optional for back-compat.
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    unit: Optional[str] = None
    mrp: Optional[float] = None


class BillLineItemOut(BillLineItemIn):
    id: int
    line_total: float

    class Config:
        from_attributes = True


class BillCreate(BaseModel):
    # 2026-09: patient account this bill is issued against (authoritative
    # patients.id). Nullable = counter/walk-in sale without a patient link.
    patient_id: Optional[int] = None
    customer_name: str
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    payment_method: Optional[str] = "cash"
    # NEW (2026-09): "paid" | "pending" | "cancelled". Default "paid".
    status: Optional[str] = "paid"
    tax_percent: float = 0.0
    discount: float = 0.0
    notes: Optional[str] = None
    items: list[BillLineItemIn]
    # NEW (2026-08): when False, the bill is a "proforma" — line items are
    # recorded but stock levels are NOT decremented. Default True to keep
    # today's behavior.
    affects_inventory: bool = True


class BillOut(BaseModel):
    id: int
    patient_id: Optional[int] = None
    bill_number: str
    customer_name: str
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    subtotal: float
    tax_percent: float
    tax_amount: float
    discount: float
    total: float
    payment_method: Optional[str] = None
    status: Optional[str] = "paid"
    notes: Optional[str] = None
    created_at: datetime
    affects_inventory: bool = True
    items: list[BillLineItemOut] = []

    class Config:
        from_attributes = True


class BillListItem(BaseModel):
    """Compact row for the billing list (no items)."""
    id: int
    bill_number: str
    customer_name: str
    total: float
    payment_method: Optional[str] = None
    status: Optional[str] = "paid"
    created_at: datetime
    affects_inventory: bool = True

    class Config:
        from_attributes = True


class BillListOut(BaseModel):
    items: list[BillListItem]
    total_count: int


# ---------- Shopkeeper: dashboard / customers / activity (2026-09) ----------

class ShopkeeperTrendPoint(BaseModel):
    """One point of a daily trend series (date + value)."""
    date: str          # ISO yyyy-mm-dd
    label: str         # friendly short label (e.g. "Mon 04")
    value: float       # money for sales, count otherwise
    count: int = 0     # number of underlying events that day


class ShopkeeperSalesStats(BaseModel):
    today_total: float
    today_count: int
    avg_bill_value: float
    prev_day_total: float
    change_vs_prev: float        # percentage, signed
    trend: list[ShopkeeperTrendPoint] = []   # last 7 days


class ShopkeeperInventoryStats(BaseModel):
    total_items: int             # rows (batches)
    total_medicines: int         # distinct medicine names
    low_stock: int
    out_of_stock: int
    expiring_soon: int
    expired: int
    inventory_value: float       # sum(quantity * unit_price)


class ShopkeeperCustomerStats(BaseModel):
    today_customers: int
    total_customers: int
    new_this_week: int
    returning_this_week: int
    trend: list[ShopkeeperTrendPoint] = []   # last 7 days distinct customers


class ShopkeeperRevenueStats(BaseModel):
    today_revenue: float
    paid_today: float
    pending_today: float
    cancelled_today: float
    total_revenue: float
    avg_bill_value: float
    trend: list[ShopkeeperTrendPoint] = []   # last 7 days


class ShopkeeperDashboardOut(BaseModel):
    greeting_name: str
    shop_name: str
    sales: ShopkeeperSalesStats
    inventory: ShopkeeperInventoryStats
    customers: ShopkeeperCustomerStats
    revenue: ShopkeeperRevenueStats
    low_stock_items: list[dict] = []
    expiring_items: list[dict] = []
    recent_bills: list[dict] = []
    attention_items: list[dict] = []


class ShopkeeperActivityOut(BaseModel):
    id: int
    action: str
    detail: str
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ShopkeeperActivityListOut(BaseModel):
    items: list[ShopkeeperActivityOut]
    total_count: int


class ShopkeeperHeatmapOut(BaseModel):
    """Daily activity counts for the last N days (oldest → newest)."""
    days: list[dict]   # [{"date": "2026-09-04", "label": "Thu 04", "count": 3}]


class ShopkeeperCustomerOut(BaseModel):
    """Aggregated customer derived from bills (no separate customer table —
    customers are the distinct (name, phone) pairs on bills)."""
    key: str                  # stable id: phone if present else lowercased name
    name: str
    phone: Optional[str] = None
    total_bills: int
    total_spent: float
    first_purchase: Optional[datetime] = None
    last_purchase: Optional[datetime] = None


class ShopkeeperCustomerListOut(BaseModel):
    items: list[ShopkeeperCustomerOut]
    total_count: int


class ShopkeeperCustomerDetailOut(ShopkeeperCustomerOut):
    bills: list[BillListItem] = []
    purchases: list[dict] = []   # per-bill item summaries


class ShopkeeperBillSearchOut(BaseModel):
    items: list[BillListItem]
    total_count: int


class MedicineIntelligenceHit(BaseModel):
    id: int
    medicine_name: str
    composition: Optional[str] = None
    manufacturer: Optional[str] = None
    batch_no: str
    mrp: Optional[float] = None
    unit_price: float
    quantity: int
    expiry_date: Optional[date] = None
    match_type: str   # exact_name | partial_name | composition | fuzzy


class MedicineIntelligenceOut(BaseModel):
    query: str
    composition_matches: list[MedicineIntelligenceHit] = []
    name_matches: list[MedicineIntelligenceHit] = []
    total_hits: int


# ---------- Patient <-> Doctor links ----------

class DoctorListItem(BaseModel):
    """Compact row for the signup dropdown and the patient 'add doctor' modal.
    Carries the doctor's specialization so the patient can pick by department."""
    id: int
    username: str
    full_name: Optional[str] = None
    specialization: Optional[str] = None
    clinic_name: Optional[str] = None

    class Config:
        from_attributes = True


class DoctorDirectoryItem(BaseModel):
    """Rich doctor record for the patient-facing discovery page (dashboard).
    Built server-side by joining User + DoctorProfile + the doctor's primary
    DoctorAddress + the doctor's primary DoctorContact. Carries enough
    metadata to render a discovery card without further round-trips."""
    id: int                                                  # User.id
    full_name: Optional[str] = None
    specialization: Optional[str] = None
    experience_years: Optional[int] = None
    hospital_name: Optional[str] = None                      # profile.clinic_name or primary address.label
    hospital_address: Optional[str] = None                   # line1, line2, city, state, pincode — formatted
    city: Optional[str] = None
    state: Optional[str] = None
    contact_value: Optional[str] = None                      # primary phone OR email
    contact_kind: Optional[str] = None                       # "phone" | "email" | "whatsapp"
    # True when the viewing patient already has an active link with this
    # doctor (only meaningful for patient-role callers).
    is_linked: bool = False

    class Config:
        from_attributes = True


class DoctorPublicProfile(BaseModel):
    """Patient-visible single-doctor profile. Only public professional
    fields the doctor configured — never credentials or internal data."""
    id: int                                                  # User.id
    full_name: Optional[str] = None
    profile_picture: Optional[str] = None                    # avatar path or null
    username: Optional[str] = None
    specialization: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    clinic_name: Optional[str] = None
    clinic_address: Optional[str] = None
    consultation_fee: Optional[str] = None
    bio: Optional[str] = None
    languages: Optional[str] = None
    available_hours: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    contact_value: Optional[str] = None                      # primary phone OR email
    contact_kind: Optional[str] = None                       # "phone" | "email" | "whatsapp"
    is_linked: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PatientDoctorLinkCreate(BaseModel):
    doctor_user_id: int
    # Optional override; if omitted the server snapshots DoctorProfile.specialization.
    department: Optional[str] = None


class PatientDoctorLinkOut(BaseModel):
    id: int
    patient_id: int
    doctor_user_id: int
    doctor_name: Optional[str] = None       # joined in from User.full_name
    department: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Doctor <-> Patient chat ----------
# A thread is the (patient_id, doctor_user_id) pair. Messages between those
# two parties share the same patient_id and doctor_user_id columns, so
# fetching a thread is a single SELECT.

class ChatAttachmentOut(BaseModel):
    id: int
    file_name: str
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    kind: str                                # "image" | "document" | "video" | "other"
    # Relative URL — the frontend prepends API_BASE before fetching.
    download_url: str


class ChatReactionOut(BaseModel):
    reaction: str
    user_id: int
    user_name: str = ""
    created_at: Optional[datetime] = None


class ChatReplyPreview(BaseModel):
    """Compact snapshot of the message being replied to."""
    id: int
    sender_user_id: int
    sender_name: str = ""
    body: Optional[str] = None
    has_attachments: bool = False
    kind: Optional[str] = None   # "image" | "document" | "video" | "text" | "deleted"


class ChatMessageOut(BaseModel):
    id: int
    patient_id: int
    doctor_user_id: int
    sender_user_id: int
    sender_role: str                         # "doctor" | "patient" (joined in from User.role)
    sender_name: str                         # joined in from User.full_name (fallback: username)
    body: Optional[str] = None
    attachments: list[ChatAttachmentOut] = []
    created_at: datetime
    # --- Interaction metadata (2026-09). All additive so older clients keep
    # working unchanged.
    edited_at: Optional[datetime] = None     # set when the sender edited it
    deleted_at: Optional[datetime] = None    # delete-for-everyone (soft)
    deleted_by_user_id: Optional[int] = None
    hidden_from_me: bool = False             # caller hid this message (delete-for-me)
    reply_to: Optional[ChatReplyPreview] = None
    reactions: list[ChatReactionOut] = []
    # True when the caller has saved/starred this message.
    saved_by_me: bool = False
    # Read state from the SENDER's perspective: does the other side appear
    # to have read this message already? Only meaningful on own messages.
    read_by_other: Optional[bool] = None


class ChatThreadOut(BaseModel):
    """One entry in the thread list. Represents the chat anchored on a single
    patient with one provider-side participant (doctor, assigned nurse, or
    hospital admin — stored in the legacy ``doctor_user_id`` column)."""

    patient_id: int
    patient_name: str
    patient_user_id: Optional[int] = None
    doctor_user_id: int
    doctor_name: str
    # --- Care-team metadata (2026-09). All optional so older clients keep
    # working unchanged.
    # Role of the provider-side participant: "doctor" | "nurse" | "hospital".
    partner_role: Optional[str] = None
    # Display title/subtitle for THIS caller's thread list (e.g. the nurse
    # sees "Priya · Assigned Nurse", the patient sees the hospital name).
    thread_title: Optional[str] = None
    thread_subtitle: Optional[str] = None
    # Department snapshot when the partner is a linked doctor.
    department: Optional[str] = None
    # The most recent message in the thread (for the preview shown in the
    # thread list). None when the thread has no messages yet.
    last_message: Optional[ChatMessageOut] = None
    # Messages I (the caller) haven't read yet. Drives the unread badge.
    unread_count: int = 0
    # --- Conversation preferences + presence (2026-09) ---
    pinned: bool = False
    archived: bool = False
    muted: bool = False                       # muted_until is in the future
    muted_until: Optional[datetime] = None
    # Partner presence within this thread (typing/online are best-effort
    # lightweight pings from the other side's pref row).
    partner_online: bool = False
    partner_last_seen: Optional[datetime] = None
    partner_typing: bool = False


class ChatThreadSearchResult(BaseModel):
    """One matching message inside a conversation search."""
    message: ChatMessageOut
    snippet: Optional[str] = None


class ChatSearchResults(BaseModel):
    query: str
    thread: ChatThreadOut
    matches: list[ChatThreadSearchResult] = []
    total: int = 0


class ChatGlobalSearchItem(BaseModel):
    thread: ChatThreadOut
    message: ChatMessageOut
    snippet: Optional[str] = None


class ChatSendIn(BaseModel):
    """Body for POST /chat/threads/{...}/messages when sending JSON (text only)."""

    body: Optional[str] = None
    reply_to_id: Optional[int] = None
    client_message_id: Optional[str] = None


class ChatReactIn(BaseModel):
    reaction: str = "👍"


class ChatEditIn(BaseModel):
    body: str


class ChatMuteIn(BaseModel):
    minutes: Optional[int] = None   # None => mute forever (until unmuted)


class ChatPinIn(BaseModel):
    pinned: bool = True


class ChatArchiveIn(BaseModel):
    archived: bool = True


class ChatTypingIn(BaseModel):
    typing: bool = True


class ChatMessagesOut(BaseModel):
    messages: list[ChatMessageOut]


class ChatLockRequest(BaseModel):
    pin: Optional[str] = None


class ChatThemeRequest(BaseModel):
    theme: str = "default"


class ChatForwardIn(BaseModel):
    """Target conversation for a forward: any thread pair the caller belongs
    to. ``body`` is an optional note the caller can add — when present it is
    appended after the original text; when absent the original text (if any)
    is copied as-is."""

    patient_id: int
    doctor_user_id: int
    body: Optional[str] = None
    client_message_id: Optional[str] = None


class ChatPrivacyOut(BaseModel):
    """The caller's own chat privacy settings."""

    show_last_seen: bool = True
    show_read_receipts: bool = True
    show_typing: bool = True


class ChatPrivacyUpdate(BaseModel):
    """Patch body for /chat/privacy — only supplied fields change."""

    show_last_seen: Optional[bool] = None
    show_read_receipts: Optional[bool] = None
    show_typing: Optional[bool] = None


# ============================================================
# Hospital: Wards / Beds / Admissions
# ============================================================
# Wire shape for ward CRUD. ``daily_rate`` and ``ward_type`` default to
# sane values so creating a basic "General Ward" is a single POST.

class WardCreate(BaseModel):
    name: str
    ward_type: Optional[str] = "general"
    daily_rate: Optional[float] = 500.0
    total_beds: Optional[int] = 10
    notes: Optional[str] = None


class WardUpdate(BaseModel):
    name: Optional[str] = None
    ward_type: Optional[str] = None
    daily_rate: Optional[float] = None
    total_beds: Optional[int] = None
    notes: Optional[str] = None


class WardOut(BaseModel):
    id: int
    name: str
    ward_type: str
    daily_rate: float
    total_beds: int
    notes: Optional[str] = None
    created_at: datetime
    # Computed: actual bed count and how many are currently occupied.
    bed_count: int = 0
    occupied_count: int = 0
    # Hospital ownership (NULL = unassigned / owned by doctor with no hospital).
    hospital_id: Optional[int] = None
    hospital_name: Optional[str] = None

    class Config:
        from_attributes = True


class BedCreate(BaseModel):
    bed_number: str
    bed_type: Optional[str] = None
    notes: Optional[str] = None


class BedBulkCreate(BaseModel):
    """Body for POST /wards/{id}/beds. Accepts a single ``beds`` array so
    a doctor can add 10 beds in one round-trip instead of clicking a
    button 10 times. The endpoint numbers them sequentially if a prefix
    is supplied (``prefix="A"`` + ``start=1`` + ``count=10`` → "A-1"…"A-10")."""
    beds: list[BedCreate] = []


class BedOut(BaseModel):
    id: int
    ward_id: int
    bed_number: str
    bed_type: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    # True when an active admission (discharged_at IS NULL) exists for this
    # bed. Computed server-side so the grid view can color cells without a
    # second roundtrip.
    is_occupied: bool = False
    current_admission_id: Optional[int] = None

    class Config:
        from_attributes = True


class AdmissionCreate(BaseModel):
    """Body for POST /admissions and POST /patients/{id}/admit. Both
    endpoints accept the same shape so the UI can hit one URL."""
    patient_id: int
    bed_id: int
    notes: Optional[str] = None


class AdmissionTransferIn(BaseModel):
    to_bed_id: int
    reason: Optional[str] = None


class AdmissionDischargeIn(BaseModel):
    reason: Optional[str] = None


class AdmissionNotesIn(BaseModel):
    notes: str


class AdmissionOut(BaseModel):
    id: int
    patient_id: int
    patient_name: Optional[str] = None     # joined in
    bed_id: int
    bed_number: Optional[str] = None       # joined in
    ward_id: int
    ward_name: Optional[str] = None        # joined in
    admitted_by: int
    admitted_by_name: Optional[str] = None  # joined in
    admitted_at: datetime
    discharged_at: Optional[datetime] = None
    discharged_by: Optional[int] = None
    discharge_reason: Optional[str] = None
    notes: Optional[str] = None
    # True when this admission is still active (discharged_at IS NULL).
    is_active: bool = True
    # Number of BedTransfer rows on this admission (0 if no transfers yet).
    transfer_count: int = 0
    # Hospital where this admission lives. NULL for legacy admissions
    # created before the hospital workflow existed; populated when a
    # hospital admin placed the patient via /hospital-admit-requests/{id}/place.
    hospital_id: Optional[int] = None
    hospital_name: Optional[str] = None
    # Currently-assigned active nurse (joined in; None if unassigned).
    assigned_nurse_user_id: Optional[int] = None
    assigned_nurse_name: Optional[str] = None

    class Config:
        from_attributes = True


class AdmissionListOut(BaseModel):
    items: list[AdmissionOut]
    total_count: int


class AdmissionChargesOut(BaseModel):
    """Computed on demand. Days is inclusive of the admit day (so a
    same-day admission is 1 day, not 0). Charges = days × ward's
    daily_rate at admission time (denormalized via Admission.ward_id_at_admit)."""
    admission_id: int
    admitted_at: datetime
    discharged_at: Optional[datetime] = None
    is_active: bool
    days: int
    daily_rate: float
    total_charges: float


# ---------- Admission requests (patient → doctor accept/reject flow) ----------

# Allowed status values, kept in one place so endpoint validation and the
# Pydantic schema agree.
ADMISSION_REQUEST_STATUSES = {"pending", "accepted", "rejected", "cancelled"}


class AdmissionRequestCreate(BaseModel):
    """Body for POST /admission-requests. Patient-only."""
    patient_id: int
    bed_id: int
    request_reason: Optional[str] = None


class AdmissionRequestDecision(BaseModel):
    """Body for POST /admission-requests/{id}/accept and .../reject."""
    reason: Optional[str] = None


class AdmissionRequestOut(BaseModel):
    id: int
    patient_id: int
    patient_name: Optional[str] = None
    bed_id: int
    bed_number: Optional[str] = None
    ward_id: Optional[int] = None
    ward_name: Optional[str] = None
    requested_by_user_id: int
    requested_at: datetime
    status: str
    decided_by_user_id: Optional[int] = None
    decided_by_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_reason: Optional[str] = None
    request_reason: Optional[str] = None
    admission_id: Optional[int] = None

    class Config:
        from_attributes = True


class AdmissionRequestListOut(BaseModel):
    items: list[AdmissionRequestOut]
    total_count: int


# ---------- Hospital / Hospital Admin ----------
# Multi-hospital: doctor picks a hospital when admitting; hospital admin
# picks the bed from their own hospital's free beds and assigns a nurse.

HOSPITAL_ADMIT_REQUEST_STATUSES = {"pending", "accepted", "rejected", "cancelled"}
DISCHARGE_APPEAL_STATUSES = {"pending_hospital", "pending_doctor", "approved", "rejected", "cancelled"}


class HospitalCreate(BaseModel):
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None


class HospitalUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None


class HospitalOut(BaseModel):
    id: int
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    member_count: int = 0
    ward_count: int = 0
    bed_count: int = 0
    created_at: datetime
    # True when the current user (role=hospital) is an admin of this hospital.
    is_mine: bool = False
    # 2026-09: patient-facing hospital directory enrichment. Doctor count +
    # department/specialty names come from doctors registered to this
    # hospital via hospital_registration_links.
    doctor_count: int = 0
    departments: list[str] = []

    class Config:
        from_attributes = True


class PatientHospitalLinkCreate(BaseModel):
    hospital_id: int


class PatientDoctorRequestOut(BaseModel):
    """Pending patient→doctor link request as seen by the patient."""
    request_id: int
    patient_id: int
    doctor_user_id: int
    doctor_name: Optional[str] = None
    doctor_specialization: Optional[str] = None
    created_at: Optional[datetime] = None


class DoctorLinkRequestOut(BaseModel):
    """Pending patient→doctor link request as seen by the doctor."""
    request_id: int
    patient_id: int
    patient_name: Optional[str] = None
    created_at: Optional[datetime] = None


class CareEventOut(BaseModel):
    """One care-network history entry (patient view)."""
    id: int
    patient_id: int
    kind: str
    target_kind: str
    doctor_user_id: Optional[int] = None
    doctor_name: Optional[str] = None
    hospital_id: Optional[int] = None
    hospital_name: Optional[str] = None
    note: Optional[str] = None
    created_at: Optional[datetime] = None


class PatientHospitalLinkOut(BaseModel):
    """One patient ↔ hospital relationship with hospital display info."""
    link_id: int
    patient_id: int
    hospital_id: int
    hospital_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    status: str = "linked"
    linked_at: Optional[datetime] = None
    unlinked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class HospitalListOut(BaseModel):
    items: list[HospitalOut]
    total_count: int


# ---- 2026-08-17: doctor-side hospital self-link ----------------------------

class DoctorHospitalRow(BaseModel):
    """One row in the doctor's "My hospitals" list (and the same shape
    used by the picker modal — `linked_to_me` is the only extra field)."""
    id: int
    name: str
    address: Optional[str] = None
    phone: Optional[str] = None
    member_count: int = 0
    ward_count: int = 0
    bed_count: int = 0
    created_at: datetime
    # True when the calling doctor is currently linked to this hospital
    # via a HospitalRegistrationLink(role='doctor') row.
    linked_to_me: bool = False
    # When linked_to_me=True, these mirror the link row so the UI can
    # render "linked since …" + a notes preview without an extra fetch.
    linked_at: Optional[datetime] = None
    link_notes: Optional[str] = None

    class Config:
        from_attributes = True


class DoctorHospitalsListOut(BaseModel):
    items: list[DoctorHospitalRow]
    total_count: int


class DoctorHospitalLinkIn(BaseModel):
    """Body for POST /doctors/me/hospitals/{id}/link. `notes` is optional
    free text the doctor wants the hospital admin to see (e.g.
    'Available Mon/Wed/Fri 9am–1pm')."""
    notes: Optional[str] = None


class DoctorHospitalLinkOut(BaseModel):
    """Return value from POST /doctors/me/hospitals/{id}/link."""
    hospital_id: int
    hospital_name: str
    linked_at: datetime
    notes: Optional[str] = None


class HospitalMemberAdd(BaseModel):
    """Body for POST /hospitals/mine/members. Add another hospital-role user
    by id to the caller's hospital. The target user's HospitalProfile is
    created or updated to point at the same hospital."""
    user_id: int


class HospitalMemberOut(BaseModel):
    user_id: int
    username: str
    full_name: Optional[str] = None
    role: str
    joined_at: datetime


class HospitalMembersOut(BaseModel):
    items: list[HospitalMemberOut]
    total_count: int


# ---------- Doctor → Hospital admit request ----------

class HospitalAdmitRequestCreate(BaseModel):
    """Body for POST /hospital-admit-requests. Doctor only."""
    patient_id: int
    hospital_id: int
    reason: Optional[str] = None


class HospitalAdmitRequestOut(BaseModel):
    id: int
    patient_id: int
    patient_name: Optional[str] = None
    hospital_id: int
    hospital_name: Optional[str] = None
    requested_by_doctor_user_id: int
    requested_by_doctor_name: Optional[str] = None
    requested_at: datetime
    status: str
    reason: Optional[str] = None
    placed_admission_id: Optional[int] = None
    # Joined for the hospital's "who is this request from" context.
    decided_at: Optional[datetime] = None
    decided_by_user_id: Optional[int] = None

    class Config:
        from_attributes = True


class HospitalAdmitRequestListOut(BaseModel):
    items: list[HospitalAdmitRequestOut]
    total_count: int


class HospitalAdmitRequestBulkDeleteOut(BaseModel):
    """Response for POST /hospital-admit-requests/bulk-delete. Pending
    requests are NOT deleted by this endpoint — they must be cancelled via
    /hospital-admit-requests/{id}/cancel so the hospital admin sees the
    withdrawal. `skipped_ids` lists both pending rows and any ids that
    weren't owned by the calling doctor (or didn't exist)."""
    deleted_count: int
    skipped_ids: list[int] = []


class PlacementIn(BaseModel):
    """Body for POST /hospital-admit-requests/{id}/place. Hospital admin
    picks a free bed in their own hospital and optionally a nurse."""
    bed_id: int
    nurse_user_id: Optional[int] = None
    notes: Optional[str] = None


# ---------- Nurse assignment ----------

class NurseAssignmentCreate(BaseModel):
    """Body for POST /admissions/{admission_id}/nurse."""
    nurse_user_id: int
    notes: Optional[str] = None


class NurseAssignmentOut(BaseModel):
    id: int
    patient_id: int
    patient_name: Optional[str] = None
    nurse_user_id: int
    nurse_name: Optional[str] = None
    assigned_by_user_id: Optional[int] = None
    assigned_by_name: Optional[str] = None
    assigned_at: datetime
    is_active: bool
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class NurseAssignmentListOut(BaseModel):
    items: list[NurseAssignmentOut]
    total_count: int


# ---- 2026-08-23: Emergency admit -------------------------------------------

class EmergencyAdmitIn(BaseModel):
    """Body for POST /emergency/admit. Caller (doctor or patient) supplies
    the hospital; the server picks the first free bed in that hospital and
    the first vacant nurse. Demographics fields are optional when a
    patient_id is supplied (existing patient); required when creating a
    brand-new patient record from a doctor-initiated emergency."""

    hospital_id: int
    # Existing patient: when supplied, demographics are ignored and the
    # server uses this patient's record. Patient callers automatically
    # fill this with their own patient_id server-side.
    patient_id: Optional[int] = None
    # Demographics — used when patient_id is None (doctor creating a new
    # patient on the fly). Optional for patient callers (defaults to their
    # own record). Field-validator strips blank strings.
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    contact: Optional[str] = None
    # Free-text reason — shown on the admission row + the queue.
    reason: Optional[str] = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class EmergencyPreviewOut(BaseModel):
    """Returned by GET /emergency/preview/{hospital_id} so the UI can show
    the user which bed + nurse the system will auto-assign before they
    confirm."""

    hospital_id: int
    hospital_name: str
    free_beds: int
    total_beds: int
    available_nurses: int
    total_nurses: int
    # The exact bed + nurse the server would pick. Useful for "Your
    # admission will be in ICU-A bed G-03 with Nurse X" preview text.
    picked_bed_id: Optional[int] = None
    picked_bed_number: Optional[str] = None
    picked_ward_id: Optional[int] = None
    picked_ward_name: Optional[str] = None
    picked_nurse_user_id: Optional[int] = None
    picked_nurse_name: Optional[str] = None


class EmergencyAdmitOut(BaseModel):
    """Returned by POST /emergency/admit. Wraps the created Admission with
    the bed/ward/hospital/nurse joins so the UI can show a confirmation
    card immediately."""

    admission: AdmissionOut
    nurse_assignment: Optional[NurseAssignmentOut] = None
    hospital_id: int
    hospital_name: str
    # True when the server auto-created a new patient row (doctor caller
    # with no patient_id supplied). Used by the UI to display the
    # freshly-generated patient_id.
    patient_created: bool = False


class NursePatientOut(BaseModel):
    """One patient the nurse is currently assigned to, with the active
    admission + hospital context. Returned by GET /nurse/patients/active."""
    patient: PatientOut
    admission_id: Optional[int] = None
    bed_number: Optional[str] = None
    ward_name: Optional[str] = None
    hospital_name: Optional[str] = None
    admitted_at: Optional[datetime] = None
    nurse_assignment_id: Optional[int] = None
    # Admitting doctor — used by the nurse's Doctor-chat modal so it
    # knows which thread to open. NULL when the admission has no
    # admitting doctor on file (very old data).
    admitting_doctor_user_id: Optional[int] = None
    admitting_doctor_name: Optional[str] = None
    # Populated for the /nurse/patients/history endpoint. NULL for the
    # active list (the patient has no discharged admission yet).
    discharged_at: Optional[datetime] = None


class NursePatientListOut(BaseModel):
    items: list[NursePatientOut]
    total_count: int


# ---------- Discharge appeal (3-step: nurse → hospital → doctor) ----------

class DischargeAppealCreate(BaseModel):
    """Body for POST /discharge-appeals. Nurse only."""
    admission_id: int
    reason: Optional[str] = None


class DischargeAppealDecision(BaseModel):
    """Body for /hospital-decide and /doctor-decide. Decision is required."""
    decision: str  # "approved" | "rejected"
    reason: Optional[str] = None


class DischargeAppealOut(BaseModel):
    id: int
    admission_id: int
    patient_id: int
    patient_name: Optional[str] = None
    hospital_id: Optional[int] = None
    hospital_name: Optional[str] = None
    bed_number: Optional[str] = None
    ward_name: Optional[str] = None
    requested_by_user_id: int
    requested_by_name: Optional[str] = None
    requested_at: datetime
    nurse_reason: Optional[str] = None
    hospital_decision: Optional[str] = None
    hospital_decided_by_user_id: Optional[int] = None
    hospital_decided_by_name: Optional[str] = None
    hospital_decided_at: Optional[datetime] = None
    hospital_reason: Optional[str] = None
    doctor_decision: Optional[str] = None
    doctor_decided_by_user_id: Optional[int] = None
    doctor_decided_by_name: Optional[str] = None
    doctor_decided_at: Optional[datetime] = None
    doctor_reason: Optional[str] = None
    status: str

    class Config:
        from_attributes = True


class DischargeAppealListOut(BaseModel):
    items: list[DischargeAppealOut]
    total_count: int


# UserOut and PatientOut reference PatientDoctorLinkOut (defined just above),
# and DoctorSummaryOut references PatientProfileOut / MedicalHistoryOut /
# PatientReportOut — all of those forward refs are resolved here.
PatientDoctorLinkOut.model_rebuild()
UserOut.model_rebuild()
PatientOut.model_rebuild()
DoctorSummaryOut.model_rebuild()


# ============================================================
# Nurse: Medications / Treatments / Notes
# ============================================================
# Logged against an active Admission (not the bare Patient row) so the
# history is tied to the hospital stay. Nurses create / update / discontinue
# entries via the manage-patient modal on the nurse dashboard; the related
# doctor sees them on their patient dashboard when a note is "sent".

class MedicationCreate(BaseModel):
    """Body for POST /admissions/{id}/medications."""
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    notes: Optional[str] = None


class MedicationUpdate(BaseModel):
    name: Optional[str] = None
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    notes: Optional[str] = None
    # "active" | "discontinued" | "completed"
    status: Optional[str] = None


class MedicationOut(BaseModel):
    id: int
    admission_id: int
    patient_id: int
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    notes: Optional[str] = None
    status: str
    started_at: datetime
    stopped_at: Optional[datetime] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MedicationListOut(BaseModel):
    items: list[MedicationOut]
    total_count: int


class MedicationBulkCreate(BaseModel):
    """Body for POST /admissions/{id}/medications/bulk. Create many
    medication rows in one round-trip so a nurse can record the full
    morning med round (e.g. 5 drugs) without 5 separate Add clicks."""
    items: list[MedicationCreate]


class TreatmentCreate(BaseModel):
    """Body for POST /admissions/{id}/treatments."""
    name: str
    category: Optional[str] = None
    description: Optional[str] = None
    schedule: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = None


class TreatmentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    schedule: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    notes: Optional[str] = None
    # "scheduled" | "in_progress" | "completed" | "cancelled"
    status: Optional[str] = None


class TreatmentOut(BaseModel):
    id: int
    admission_id: int
    patient_id: int
    name: str
    category: Optional[str] = None
    description: Optional[str] = None
    schedule: Optional[str] = None
    notes: Optional[str] = None
    status: str
    scheduled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by_user_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TreatmentListOut(BaseModel):
    items: list[TreatmentOut]
    total_count: int


class TreatmentBulkCreate(BaseModel):
    """Body for POST /admissions/{id}/treatments/bulk."""
    items: list[TreatmentCreate]


class NurseNoteCreate(BaseModel):
    """Body for POST /admissions/{id}/nurse-notes (JSON mode, text only).
    For attachments, use multipart/form-data instead — see the create
    endpoint. Set ``send_to_doctor=True`` to also enqueue a NotificationEvent
    for the patient's admitting doctor."""
    note: str
    category: Optional[str] = None    # "vitals" | "observation" | "incident" | "other"
    send_to_doctor: bool = False


class NurseNoteAttachmentOut(BaseModel):
    """One file attached to a nurse-note. Same shape as ChatAttachmentOut
    so the chat-bubble renderer can use the same code path for both."""
    id: int
    file_name: str
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    kind: str                                # "image" | "document" | "video" | "other"
    download_url: str


class NurseNoteOut(BaseModel):
    id: int
    admission_id: int
    patient_id: int
    patient_name: Optional[str] = None
    author_user_id: Optional[int] = None
    author_name: Optional[str] = None
    note: str
    category: Optional[str] = None
    sent_to_doctor: bool
    summary: Optional[str] = None
    summary_generated_at: Optional[datetime] = None
    # 2026-08: "free_text" | "report_aggregate" — NULL is treated as
    # free_text by the renderer.
    kind: Optional[str] = None
    created_at: datetime
    attachments: list[NurseNoteAttachmentOut] = []

    class Config:
        from_attributes = True


class NurseNoteSummaryOut(BaseModel):
    """Body for POST /nurse-notes/{id}/summarize — the regenerated AI summary."""
    note_id: int
    summary: str
    summary_generated_at: datetime


class NurseNoteListOut(BaseModel):
    items: list[NurseNoteOut]
    total_count: int


# =====================================================================
# PatientRecords (2026-08)
# ---------------------------------------------------------------------
# Aggregated "one patient, everything about them" bundle plus a search
# shape used by the doctor/hospital/nurse PatientRecords page. Built to
# coexist with the existing per-resource endpoints — the bundle just
# does the JOINs server-side so the page renders in a single roundtrip.
# =====================================================================

# Allowed gender values; both PatientWrite and the search filter share this
# so the API stays consistent (UI pickers render the same options).
PATIENTRECORDS_GENDERS = {"male", "female", "other", "unknown"}


class PatientRecordSearchResult(BaseModel):
    """A single row in the PatientRecords search results list.

    Compact by design: enough for the list view (avatar, name, age,
    gender, place, Aadhaar, treatment summary) — the full bundle is a
    separate request so search results don't carry N Mb of medical
    history each.
    """
    patient_id: int
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    contact: Optional[str] = None
    aadhar_number: Optional[str] = None
    place: Optional[str] = None
    # Aggregate counts so the row can show "12 records · 4 reports · "
    # badges without re-querying per row.
    records_count: int = 0
    prescriptions_count: int = 0
    reports_count: int = 0
    history_count: int = 0
    # Linked doctor (if any). Single value because each patient usually
    # has a primary GP — the full M:N is in the bundle.
    primary_linked_doctor_id: Optional[int] = None
    primary_linked_doctor_name: Optional[str] = None
    # Whether a User account exists for this patient (so the doctor
    # knows whether they can already self-login).
    has_user_account: bool = False
    user_id: Optional[int] = None
    username: Optional[str] = None
    created_at: datetime


class PatientRecordSearchOut(BaseModel):
    items: list[PatientRecordSearchResult]
    total_count: int
    # Echo back the filters actually applied so the UI can show
    # "Showing 12 of 348 — filter: city=Pune".
    applied_filters: dict


class PatientRecordBundle(BaseModel):
    """Everything we know about one patient, returned in a single GET.

    Doctor/hospital/nurse pages open with this — it populates the
    detail panel with history + records + prescriptions + reports +
    scans + reminders + vitals + admissions in one roundtrip.

    Note: the SQLAlchemy MedicalRecord model is serialised as
    ``RecordOut`` (existing schema name), and VitalRecord serialises
    as ``VitalOut``. We type the bundle after the existing shapes so
    the JS can use the same shape keys it already knows.
    """
    patient: PatientOut
    profile: Optional["PatientProfileOut"] = None
    history: list["MedicalHistoryOut"] = []
    records: list["RecordOut"] = []
    prescriptions: list["PrescriptionOut"] = []
    vitals: list["VitalOut"] = []
    reminders: list["ReminderOut"] = []
    reports: list["PatientReportOut"] = []
    documents: list["DocumentScanOut"] = []
    admissions: list["AdmissionOut"] = []
    # Links: doctor↔patient M:N so the panel can show "also consulted by Dr. X".
    linked_doctors: list["PatientDoctorLinkOut"] = []
    # AI-generated clinical summary (plain-language paragraph).
    ai_summary: Optional[str] = None
    # Quick stats for the summary header.
    total_visits: int = 0
    active_prescriptions: int = 0
    last_visit: Optional[str] = None


# Forward references get re-resolved at import time below. Putting the
# PatientRecordsBundle at the bottom of the file keeps the original
# schema ordering intact while still referencing forward models.
PatientOut.model_rebuild()
PatientProfileOut.model_rebuild()
MedicalHistoryOut.model_rebuild()
RecordOut.model_rebuild()
PrescriptionOut.model_rebuild()
VitalOut.model_rebuild()
ReminderOut.model_rebuild()
PatientReportOut.model_rebuild()
DocumentScanOut.model_rebuild()
AdmissionOut.model_rebuild()
PatientDoctorLinkOut.model_rebuild()


# PatientRecords: PATCH body for the self-edit page. Fields are all
# Optional — the patient only sends what they want to change; empty
# strings are coerced to None so SQLite doesn't keep "".split() rows of
# garbage data.
class PatientSelfProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    contact: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    place: Optional[str] = None
    aadhar_number: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    # Free-form notes ("allergies", "on blood thinners", …) — mirrors the
    # PatientProfile column.
    notes: Optional[str] = None

    # Trim empty strings to None so partial saves don't leave "" in the DB.
    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class PatientMedicalHistoryUpsert(BaseModel):
    """Patient-issued create/update of a MedicalHistoryEntry. Patients
    can self-edit their own medical history (allergies / family
    history / prior surgeries etc.); doctors retain create permission
    via /patients/{id}/history.

    Maps to the existing MedicalHistoryEntry columns — ``description``
    becomes ``details`` server-side because the model column is named
    ``details``."""
    category: str = "other"
    title: str
    description: Optional[str] = None
    diagnosed_year: Optional[int] = None
    is_ongoing: bool = True


# --- 2026-08: nurse transfer / send-back flows -------------------------------
class TransferNurseIn(BaseModel):
    """Body for POST /admissions/{id}/transfer-nurse. Nurse hands the
    patient to another nurse at the same hospital."""
    new_nurse_user_id: int
    reason: Optional[str] = None


class TransferNurseOut(BaseModel):
    admission_id: int
    patient_id: int
    old_nurse_user_id: int
    new_assignment: "NurseAssignmentOut"
    reason: Optional[str] = None


# Allowed statuses for medications + treatments, kept here so endpoint
# validators and the frontend agree.
ALLOWED_MEDICATION_STATUSES = {"active", "discontinued", "completed"}
ALLOWED_TREATMENT_STATUSES = {"scheduled", "in_progress", "completed", "cancelled"}
ALLOWED_NURSE_NOTE_CATEGORIES = {"vitals", "observation", "incident", "shift_update", "other"}
# 2026-08: distinguishes free-text nurse reports from AI-aggregated
# reports. NULL on the model side is treated as "free_text".
ALLOWED_NURSE_NOTE_KINDS = {"free_text", "report_aggregate"}


# ---------- 2026-08-17: Hospital member management + appointments ----------

# ---- Nurse profile ----
class NurseProfileCreate(BaseModel):
    """Body for POST /hospitals/mine/register/nurse and PATCH /.../profile/nurse/{user_id}.
    All fields optional so partial updates work in PATCH."""
    department: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    current_address: Optional[str] = None
    permanent_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    languages: Optional[str] = None
    bio: Optional[str] = None
    shift_preference: Optional[str] = None
    registration_number: Optional[str] = None


class NurseProfileOut(NurseProfileCreate):
    id: int
    user_id: int
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---- Nurse registration (the rich version that also creates the User) ----
class NurseRegisterIn(BaseModel):
    """Body for POST /hospitals/mine/register/nurse."""
    # Identity — admin picks a username + initial password. The new account
    # can be shared with the nurse later via the standard reset flow.
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    initial_password: Optional[str] = None  # if blank, the system auto-generates
    profile: Optional[NurseProfileCreate] = None


# ---- Doctor registration (rich, with full profile payload) ----
class DoctorRegisterIn(BaseModel):
    """Body for POST /hospitals/mine/register/doctor.
    Reuses existing DoctorProfile fields. If username matches an existing
    doctor, the server attaches the new profile data to that user instead of
    creating a duplicate."""
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    initial_password: Optional[str] = None
    profile: Optional[DoctorProfileCreate] = None


# ---- Patient registration (rich + medical history + media) ----
class MedicalHistoryEntryIn(BaseModel):
    """One medical-history row embedded in the patient registration payload."""
    category: str                    # "condition" | "allergy" | "surgery" | "medication" | "family_history" | "immunization" | "other"
    title: str
    details: Optional[str] = None
    diagnosed_year: Optional[int] = None
    is_ongoing: Optional[bool] = False


class PatientRegisterIn(BaseModel):
    """Body for POST /hospitals/mine/register/patient.
    Media files come on the multipart side (file=...) — this body carries the
    JSON part. Medical history entries are created server-side at registration."""
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    initial_password: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    emergency_contact_relation: Optional[str] = None
    occupation: Optional[str] = None
    marital_status: Optional[str] = None
    smoking: Optional[str] = None
    alcohol: Optional[str] = None
    diet: Optional[str] = None
    exercise: Optional[str] = None
    sleep_hours: Optional[str] = None
    medical_history: Optional[list[MedicalHistoryEntryIn]] = None


class HospitalDoctorRow(BaseModel):
    """One row in GET /hospitals/mine/doctors — every doctor globally,
    enriched with their DoctorProfile + a flag showing whether they're
    registered with this hospital."""
    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    specialization: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    clinic_name: Optional[str] = None
    consultation_fee: Optional[str] = None
    registered_with_this_hospital: bool = False
    registration_link_id: Optional[int] = None

    class Config:
        from_attributes = True


class HospitalDoctorsListOut(BaseModel):
    items: list[HospitalDoctorRow]
    total_count: int


class HospitalNurseRow(BaseModel):
    """One row in GET /hospitals/mine/nurses — every nurse globally."""
    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    qualifications: Optional[str] = None
    experience_years: Optional[int] = None
    registered_with_this_hospital: bool = False
    registration_link_id: Optional[int] = None

    class Config:
        from_attributes = True


class HospitalNursesListOut(BaseModel):
    items: list[HospitalNurseRow]
    total_count: int


class HospitalPatientRow(BaseModel):
    """One row in GET /hospitals/mine/patients — patients registered with
    this hospital (and any patient globally, so admins can see newcomers)."""
    id: int                                      # Patient.id
    user_id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    blood_group: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    city: Optional[str] = None
    registered_with_this_hospital: bool = False
    registration_link_id: Optional[int] = None
    medical_history_count: int = 0
    report_count: int = 0

    class Config:
        from_attributes = True


class HospitalPatientsListOut(BaseModel):
    items: list[HospitalPatientRow]
    total_count: int


class HospitalRegistrationLinkOut(BaseModel):
    id: int
    hospital_id: int
    user_id: int
    role: str
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RegisterResponseOut(BaseModel):
    """Generic 200 response after a hospital-admin registration action.
    Carries the new/updated user_id + patient_id + initial credentials so the
    admin can share them with the new person."""
    user_id: int
    patient_id: Optional[int] = None
    username: str
    initial_password: Optional[str] = None
    registration_link_id: int


# ---- Appointments ----
ALLOWED_APPOINTMENT_STATUSES = {"scheduled", "confirmed", "completed", "cancelled", "no_show"}


class AppointmentCreateIn(BaseModel):
    """Body for POST /hospitals/mine/appointments (admin) and
    POST /patients/mine/appointments (patient self-book).
    hospital_id is derived from the admin's hospital on admin-book; on
    patient self-book the server picks the hospital from the chosen doctor
    (a doctor's `linked_hospital_id` is set when they register with a hospital)."""
    doctor_user_id: Optional[int] = None
    patient_id: int
    scheduled_at: datetime
    duration_minutes: Optional[int] = 30
    room_number: Optional[str] = None
    department: Optional[str] = None
    reason: Optional[str] = None


class AppointmentStatusUpdateIn(BaseModel):
    status: str  # validated against ALLOWED_APPOINTMENT_STATUSES in the route
    feedback: Optional[str] = None
    feedback_rating: Optional[int] = None  # 1-5 stars


class AppointmentOut(BaseModel):
    id: int
    hospital_id: int
    hospital_name: Optional[str] = None
    doctor_user_id: int
    doctor_name: Optional[str] = None
    doctor_specialization: Optional[str] = None
    patient_id: int
    patient_name: Optional[str] = None
    patient_username: Optional[str] = None
    scheduled_at: datetime
    duration_minutes: int
    room_number: Optional[str] = None
    department: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None
    status: str
    appointment_type: Optional[str] = "consultation"
    priority: Optional[str] = "normal"
    confirmed_at: Optional[datetime] = None
    rescheduled_from: Optional[int] = None
    booked_by_patient: bool
    feedback: Optional[str] = None
    feedback_rating: Optional[int] = None
    feedback_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        extra = "allow"


class AppointmentListOut(BaseModel):
    items: list[AppointmentOut]
    upcoming_count: int
    past_count: int
    total_count: int = 0


class AppointmentStatsOut(BaseModel):
    total: int = 0
    today: int = 0
    upcoming: int = 0
    pending: int = 0
    confirmed: int = 0
    completed: int = 0
    cancelled: int = 0
    no_show: int = 0
    available_doctors: int = 0


class AppointmentRescheduleIn(BaseModel):
    scheduled_at: datetime
    duration_minutes: Optional[int] = None


# ---------- MediEcho: AI clinical conversation intelligence ----------
# Two-table model: a session (the recording) + a note (the extracted,
# editable, optionally-saved clinical note).

ALLOWED_MEDIECHO_SPEAKERS = {"doctor", "patient"}


class MediEchoSegment(BaseModel):
    """One turn of the conversation, attributed to a speaker."""
    start: float = 0.0
    end: float = 0.0
    speaker: str                              # "doctor" | "patient"
    text: str


class MediEchoSessionCreate(BaseModel):
    """Body for POST /mediecho/sessions. Optional — the doctor can also
    POST with multipart for an audio file in one shot."""
    title: Optional[str] = None
    patient_id: Optional[int] = None


class MediEchoSessionOut(BaseModel):
    id: int
    # Nullable so patient accounts can create their own sessions.
    doctor_user_id: Optional[int] = None
    patient_id: Optional[int] = None
    patient_name: Optional[str] = None
    title: Optional[str] = None
    audio_filename: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    duration_seconds: Optional[float] = None
    stt_model: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Convenience flag: a note row exists for this session.
    has_note: bool = False

    class Config:
        from_attributes = True


class MediEchoSessionListOut(BaseModel):
    items: list[MediEchoSessionOut]
    total_count: int


class MediEchoTranscribeSegment(BaseModel):
    start: float
    end: float
    speaker: str
    text: str


class MediEchoTranscribeOut(BaseModel):
    session_id: int
    status: str
    duration: float
    model: str
    language: str
    segments: list[MediEchoTranscribeSegment]


class MediEchoHighlight(BaseModel):
    kind: str             # red_flag | allergy | med_change | follow_up | test_order
    quote: str
    why: str


class MediEchoPrescribedMedicine(BaseModel):
    name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    notes: Optional[str] = None


class MediEchoExtractedNote(BaseModel):
    """The structured fields Gemini extracted from the transcript.
    Mirrors the dict returned by gemini_client.analyze_conversation."""
    chief_complaint: Optional[str] = None
    symptoms: list[str] = []
    history: list[str] = []
    allergies: list[str] = []
    current_medications: list[str] = []
    duration: Optional[str] = None
    severity: str = "unknown"
    doctor_observations: list[str] = []
    prescribed_medicines: list[MediEchoPrescribedMedicine] = []
    recommended_tests: list[str] = []
    referrals: list[str] = []
    follow_ups: list[str] = []
    summary: str = ""
    highlights: list[MediEchoHighlight] = []


class MediEchoAnalyzeOut(BaseModel):
    session_id: int
    status: str
    extracted: MediEchoExtractedNote
    transcript: list[MediEchoTranscribeSegment] = []


class MediEchoNoteOut(BaseModel):
    id: int
    session_id: int
    extracted: Optional[MediEchoExtractedNote] = None
    summary: Optional[str] = None
    highlights: list[MediEchoHighlight] = []
    edited: Optional[MediEchoExtractedNote] = None
    status: str
    approved_at: Optional[datetime] = None
    saved_at: Optional[datetime] = None
    saved_to_record_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MediEchoNoteUpdate(BaseModel):
    """Body for PUT /mediecho/sessions/{id}/note. The doctor can edit any
    field of the structured note before approving / saving."""
    chief_complaint: Optional[str] = None
    symptoms: Optional[list[str]] = None
    history: Optional[list[str]] = None
    allergies: Optional[list[str]] = None
    current_medications: Optional[list[str]] = None
    duration: Optional[str] = None
    severity: Optional[str] = None
    doctor_observations: Optional[list[str]] = None
    prescribed_medicines: Optional[list[MediEchoPrescribedMedicine]] = None
    recommended_tests: Optional[list[str]] = None
    referrals: Optional[list[str]] = None
    follow_ups: Optional[list[str]] = None
    summary: Optional[str] = None


class MediEchoSaveToRecordOut(BaseModel):
    """Result of POST /mediecho/sessions/{id}/save-to-record."""
    medical_record_id: int
    prescription_ids: list[int] = []
    note_status: str


# ---- 2026-08-23: AI Doctor chat (patient) ----------------------------------

class AIDoctorSessionCreate(BaseModel):
    """Body for POST /ai-doctor/sessions. Patients only. Optional `title`
    seeds the session name; otherwise the server auto-generates it
    from the first user message."""
    title: Optional[str] = None
    # Optional seed message: when supplied, the server immediately
    # appends it as the first user turn and generates an AI reply.
    # Useful for the "New chat" flow where the patient types their
    # first question on the same screen.
    initial_message: Optional[str] = None


class AIDoctorSessionOut(BaseModel):
    id: int
    patient_id: Optional[int] = None
    user_id: int
    title: Optional[str] = None
    status: str
    summary: Optional[str] = None
    created_at: datetime
    last_active_at: datetime
    # Denormalized counts so the history sidebar can render
    # "12 messages · 2h ago" without an extra query.
    message_count: int = 0
    # Last-turn preview: the assistant's most recent message body,
    # truncated. Empty when no assistant reply yet.
    last_message_preview: Optional[str] = None

    class Config:
        from_attributes = True


class AIDoctorSessionListOut(BaseModel):
    items: list[AIDoctorSessionOut]
    total_count: int


class AIDoctorMessageOut(BaseModel):
    id: int
    session_id: int
    role: str
    content: Optional[str] = None
    media_filename: Optional[str] = None
    media_mime: Optional[str] = None
    media_size: Optional[int] = None
    # Public URL the frontend can load the image from. Built server-side
    # from UPLOAD_DIR + media_stored_path.
    media_url: Optional[str] = None
    status: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AIDoctorMessageCreate(BaseModel):
    """Body for POST /ai-doctor/sessions/{id}/messages (text only;
    media uploads go through the multipart /messages-with-media
    endpoint to keep the JSON path clean)."""
    content: str

    @field_validator("content", mode="before")
    @classmethod
    def _strip_blank(cls, v):
        if isinstance(v, str):
            v = v.strip()
        return v


class AIDoctorMessageListOut(BaseModel):
    items: list[AIDoctorMessageOut]
    total_count: int


class AIDoctorSummaryOut(BaseModel):
    """Result of POST /ai-doctor/sessions/{id}/summarize. `text` is the
    generated summary; `generated` is True when the server freshly
    generated it this call, False when the cached version was returned."""
    text: str
    generated: bool
    generated_at: Optional[datetime] = None


class AIDoctorSessionDetailOut(BaseModel):
    """Wraps a session + its full message list. Returned by
    GET /ai-doctor/sessions/{id} so the chat UI can hydrate in one call."""
    session: AIDoctorSessionOut
    messages: list[AIDoctorMessageOut] = []


class AIDoctorSettingsOut(BaseModel):
    """Returned by GET /ai-doctor/settings. The UI swaps 'Dr. Mira' for
    persona_name everywhere a label appears."""
    user_id: int
    persona_name: str
    persona_avatar_url: Optional[str] = None
    updated_at: datetime


class AIDoctorSettingsUpdate(BaseModel):
    """Body for PUT /ai-doctor/settings. persona_name is the only field
    we let the patient customize; the safety preamble itself stays
    locked down on the server."""
    persona_name: str
    persona_avatar_url: Optional[str] = None

    @field_validator("persona_name", mode="before")
    @classmethod
    def _strip_blank(cls, v):
        if isinstance(v, str):
            v = v.strip()
        return v


# ===========================================================================
# OPD MANAGEMENT SCHEMAS
# ===========================================================================

class OPDVisitCreate(BaseModel):
    hospital_id: int
    patient_id: int
    doctor_user_id: int
    department: Optional[str] = None
    chief_complaint: Optional[str] = None
    symptoms: Optional[str] = None
    diagnosis: Optional[str] = None
    treatment_given: Optional[str] = None
    prescriptions: Optional[str] = None
    advice: Optional[str] = None
    follow_up_date: Optional[date] = None
    consultation_fee: Optional[float] = None
    fee_paid: bool = False
    payment_method: Optional[str] = None
    notes: Optional[str] = None


class OPDVisitUpdate(BaseModel):
    diagnosis: Optional[str] = None
    treatment_given: Optional[str] = None
    prescriptions: Optional[str] = None
    advice: Optional[str] = None
    follow_up_date: Optional[date] = None
    consultation_fee: Optional[float] = None
    fee_paid: Optional[bool] = None
    payment_method: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class OPDVisitOut(BaseModel):
    id: int
    hospital_id: int
    patient_id: int
    patient_name: Optional[str] = None
    doctor_user_id: int
    doctor_name: Optional[str] = None
    visit_number: str
    department: Optional[str] = None
    chief_complaint: Optional[str] = None
    diagnosis: Optional[str] = None
    status: str
    consultation_fee: Optional[float] = None
    fee_paid: bool
    follow_up_date: Optional[date] = None
    created_at: datetime
    model_config = {"from_attributes": True}


# ===========================================================================
# IPD MANAGEMENT SCHEMAS
# ===========================================================================

class IPDDiagnosisCreate(BaseModel):
    admission_id: int
    patient_id: int
    diagnosis_type: Optional[str] = None
    icd_code: Optional[str] = None
    description: str
    notes: Optional[str] = None


class IPDDiagnosisOut(BaseModel):
    id: int
    admission_id: int
    patient_id: int
    diagnosis_type: Optional[str] = None
    icd_code: Optional[str] = None
    description: str
    is_resolved: bool
    diagnosed_at: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class IPDDailyRecordCreate(BaseModel):
    admission_id: int
    patient_id: int
    record_date: date
    temperature: Optional[float] = None
    pulse: Optional[int] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2: Optional[float] = None
    weight_kg: Optional[float] = None
    progress_notes: Optional[str] = None
    diet: Optional[str] = None
    output_urine_ml: Optional[int] = None
    iv_fluids: Optional[str] = None


class IPDDailyRecordOut(BaseModel):
    id: int
    admission_id: int
    patient_id: int
    record_date: date
    temperature: Optional[float] = None
    pulse: Optional[int] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2: Optional[float] = None
    weight_kg: Optional[float] = None
    progress_notes: Optional[str] = None
    diet: Optional[str] = None
    recorded_by: Optional[int] = None
    created_at: datetime
    model_config = {"from_attributes": True}


# ===========================================================================
# LABORATORY SCHEMAS
# ===========================================================================

class LabTestCatalogCreate(BaseModel):
    hospital_id: Optional[int] = None
    name: str
    code: Optional[str] = None
    category: Optional[str] = None
    specimen_type: Optional[str] = None
    turnaround_hours: Optional[int] = None
    price: Optional[float] = None
    reference_range: Optional[str] = None


class LabTestCatalogOut(BaseModel):
    id: int
    hospital_id: Optional[int] = None
    name: str
    code: Optional[str] = None
    category: Optional[str] = None
    specimen_type: Optional[str] = None
    price: Optional[float] = None
    is_active: bool
    model_config = {"from_attributes": True}


class LabOrderCreate(BaseModel):
    hospital_id: int
    patient_id: int
    admission_id: Optional[int] = None
    opd_visit_id: Optional[int] = None
    priority: str = "routine"
    clinical_notes: Optional[str] = None
    test_ids: list[int] = []           # list of LabTestCatalog ids
    test_names: list[str] = []         # or free-text test names


class LabOrderItemOut(BaseModel):
    id: int
    test_name: str
    status: str
    result_id: Optional[int] = None
    model_config = {"from_attributes": True}


class LabOrderOut(BaseModel):
    id: int
    hospital_id: int
    patient_id: int
    patient_name: Optional[str] = None
    ordered_by: int
    doctor_name: Optional[str] = None
    order_number: str
    priority: str
    status: str
    clinical_notes: Optional[str] = None
    items: list[LabOrderItemOut] = []
    ordered_at: datetime
    completed_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class LabResultCreate(BaseModel):
    order_id: int
    order_item_id: int
    patient_id: int
    test_name: str
    result_value: Optional[str] = None
    result_numeric: Optional[float] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    flag: Optional[str] = None
    notes: Optional[str] = None
    status: str = "final"


class LabResultOut(BaseModel):
    id: int
    order_id: int
    test_name: str
    result_value: Optional[str] = None
    result_numeric: Optional[float] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    flag: Optional[str] = None
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}


# ===========================================================================
# RADIOLOGY SCHEMAS
# ===========================================================================

class RadiologyExamCatalogCreate(BaseModel):
    hospital_id: Optional[int] = None
    name: str
    modality: str
    body_part: Optional[str] = None
    price: Optional[float] = None
    turnaround_hours: Optional[int] = None
    preparation_notes: Optional[str] = None


class RadiologyExamCatalogOut(BaseModel):
    id: int
    hospital_id: Optional[int] = None
    name: str
    modality: str
    body_part: Optional[str] = None
    price: Optional[float] = None
    is_active: bool
    model_config = {"from_attributes": True}


class RadiologyOrderCreate(BaseModel):
    hospital_id: int
    patient_id: int
    admission_id: Optional[int] = None
    opd_visit_id: Optional[int] = None
    exam_catalog_id: Optional[int] = None
    exam_name: str
    modality: Optional[str] = None
    body_part: Optional[str] = None
    clinical_history: Optional[str] = None
    priority: str = "routine"


class RadiologyOrderOut(BaseModel):
    id: int
    hospital_id: int
    patient_id: int
    patient_name: Optional[str] = None
    ordered_by: int
    doctor_name: Optional[str] = None
    exam_name: str
    modality: Optional[str] = None
    order_number: str
    priority: str
    status: str
    scheduled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class RadiologyReportCreate(BaseModel):
    order_id: int
    patient_id: int
    findings: Optional[str] = None
    impression: Optional[str] = None
    recommendation: Optional[str] = None
    status: str = "final"


class RadiologyReportOut(BaseModel):
    id: int
    order_id: int
    patient_id: int
    findings: Optional[str] = None
    impression: Optional[str] = None
    recommendation: Optional[str] = None
    status: str
    created_at: datetime
    reported_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# ===========================================================================
# INSURANCE SCHEMAS
# ===========================================================================

class InsuranceProviderCreate(BaseModel):
    name: str
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None


class InsuranceProviderOut(BaseModel):
    id: int
    name: str
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    is_active: bool
    model_config = {"from_attributes": True}


class InsurancePolicyCreate(BaseModel):
    patient_id: int
    provider_id: int
    policy_number: str
    policyholder_name: Optional[str] = None
    relationship_to_patient: Optional[str] = None
    plan_type: Optional[str] = None
    sum_insured: Optional[float] = None
    deductible: Optional[float] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    notes: Optional[str] = None


class InsurancePolicyOut(BaseModel):
    id: int
    patient_id: int
    patient_name: Optional[str] = None
    provider_id: int
    provider_name: Optional[str] = None
    policy_number: str
    policyholder_name: Optional[str] = None
    sum_insured: Optional[float] = None
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class InsuranceClaimCreate(BaseModel):
    hospital_id: int
    patient_id: int
    policy_id: int
    admission_id: Optional[int] = None
    opd_visit_id: Optional[int] = None
    total_amount: float
    diagnosis_code: Optional[str] = None
    treatment_description: Optional[str] = None
    notes: Optional[str] = None


class InsuranceClaimOut(BaseModel):
    id: int
    hospital_id: int
    patient_id: int
    patient_name: Optional[str] = None
    policy_id: int
    claim_number: str
    total_amount: float
    approved_amount: Optional[float] = None
    status: str
    diagnosis_code: Optional[str] = None
    treatment_description: Optional[str] = None
    submitted_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class InsuranceClaimUpdate(BaseModel):
    status: Optional[str] = None
    approved_amount: Optional[float] = None
    rejected_amount: Optional[float] = None
    decision_notes: Optional[str] = None


# ===========================================================================
# PHARMACY / INVOICE SCHEMAS
# ===========================================================================

class HospitalPharmacyStockCreate(BaseModel):
    hospital_id: int
    medicine_name: str
    generic_name: Optional[str] = None
    batch_no: str
    manufacturer: Optional[str] = None
    quantity: int = 0
    unit: Optional[str] = None
    purchase_price: Optional[float] = None
    mrp: Optional[float] = None
    selling_price: Optional[float] = None
    expiry_date: Optional[date] = None
    low_stock_threshold: int = 10
    notes: Optional[str] = None


class HospitalPharmacyStockOut(BaseModel):
    id: int
    hospital_id: int
    medicine_name: str
    generic_name: Optional[str] = None
    batch_no: str
    manufacturer: Optional[str] = None
    quantity: int
    unit: Optional[str] = None
    purchase_price: Optional[float] = None
    mrp: Optional[float] = None
    selling_price: Optional[float] = None
    expiry_date: Optional[date] = None
    is_low_stock: bool = False
    is_expired: bool = False
    model_config = {"from_attributes": True}


class HospitalInvoiceCreate(BaseModel):
    hospital_id: int
    patient_id: int
    admission_id: Optional[int] = None
    opd_visit_id: Optional[int] = None
    discount: float = 0.0
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    items: list[dict] = []             # [{stock_id, medicine_name, quantity, unit_price, mrp}]


class HospitalInvoiceItemOut(BaseModel):
    id: int
    medicine_name: str
    quantity: int
    unit_price: float
    mrp: Optional[float] = None
    line_total: float
    model_config = {"from_attributes": True}


class HospitalInvoiceOut(BaseModel):
    id: int
    hospital_id: int
    patient_id: int
    patient_name: Optional[str] = None
    invoice_number: str
    subtotal: float
    discount: float
    tax: float
    total: float
    payment_method: Optional[str] = None
    payment_status: str
    items: list[HospitalInvoiceItemOut] = []
    created_at: datetime
    model_config = {"from_attributes": True}


# ===========================================================================
# PURCHASE MANAGEMENT SCHEMAS
# ===========================================================================

class PurchaseOrderCreate(BaseModel):
    hospital_id: int
    supplier_name: Optional[str] = None
    supplier_contact: Optional[str] = None
    discount: float = 0.0
    tax: float = 0.0
    payment_method: Optional[str] = None
    expected_date: Optional[date] = None
    notes: Optional[str] = None
    items: list[dict] = []             # [{medicine_name, generic_name, batch_no, manufacturer, quantity, unit, purchase_price, mrp, expiry_date}]


class PurchaseOrderItemOut(BaseModel):
    id: int
    medicine_name: str
    generic_name: Optional[str] = None
    batch_no: Optional[str] = None
    quantity_ordered: int
    quantity_received: int
    purchase_price: float
    mrp: Optional[float] = None
    line_total: float
    model_config = {"from_attributes": True}


class PurchaseOrderOut(BaseModel):
    id: int
    hospital_id: int
    order_number: str
    supplier_name: Optional[str] = None
    total_amount: float
    grand_total: float
    payment_status: str
    order_status: str
    expected_date: Optional[date] = None
    received_date: Optional[date] = None
    items: list[PurchaseOrderItemOut] = []
    created_at: datetime
    model_config = {"from_attributes": True}


class PurchaseOrderUpdate(BaseModel):
    payment_status: Optional[str] = None
    order_status: Optional[str] = None
    received_date: Optional[date] = None
    notes: Optional[str] = None


class PurchaseOrderReceiveItem(BaseModel):
    item_id: int
    quantity_received: int


class PurchaseOrderReceive(BaseModel):
    items: list[PurchaseOrderReceiveItem] = []
    auto_add_to_stock: bool = True


# ---------- Admission Queue (Hospital) ----------

class AdmissionQueueCreate(BaseModel):
    """Body for POST /admission-queue — hospital admin adds a patient."""
    patient_id: int
    doctor_user_id: Optional[int] = None
    department: Optional[str] = None
    admission_type: str = "normal"       # normal | emergency | surgery
    priority: str = "normal"             # normal | high | emergency
    reason: Optional[str] = None
    notes: Optional[str] = None


class AdmissionQueueOut(BaseModel):
    id: int
    hospital_id: int
    patient_id: int
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    doctor_user_id: Optional[int] = None
    doctor_name: Optional[str] = None
    department: Optional[str] = None
    admission_type: str = "normal"
    priority: str = "normal"
    status: str = "waiting"
    queue_number: int = 0
    reason: Optional[str] = None
    notes: Optional[str] = None
    waiting_since: Optional[datetime] = None
    called_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    admitted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    on_hold_since: Optional[datetime] = None
    transferred_department: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdmissionQueueListOut(BaseModel):
    items: list[AdmissionQueueOut]
    total_count: int
    page: int = 1
    page_size: int = 20


class AdmissionQueueUpdate(BaseModel):
    """Partial update for queue entry actions."""
    priority: Optional[str] = None
    status: Optional[str] = None
    department: Optional[str] = None
    notes: Optional[str] = None


class AdmissionQueueStats(BaseModel):
    """KPI statistics for the dashboard cards."""
    patients_waiting: int = 0
    emergency_admissions: int = 0
    currently_processing: int = 0
    admitted_today: int = 0
    available_beds: int = 0
    total_beds: int = 0
    average_waiting_time_min: float = 0.0



class AdmissionQueueBulkDeleteOut(BaseModel):
    deleted_count: int = 0


# ---------- Patient basic-info lookup (for Patient ID → Name auto-fill) ----------

class PatientBasicInfo(BaseModel):
    """Minimal patient info returned by GET /patients/{id}/basic-info."""
    patient_id: int
    name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    contact: Optional[str] = None

    class Config:
        from_attributes = True


# ---------- AI Report history (auto-saved from AI features) ----------

class AiReportOut(BaseModel):
    id: int
    patient_id: int
    feature_type: str
    report_type: str
    title: str
    generated_result: Optional[str] = None
    findings: Optional[str] = None
    recommendations: Optional[str] = None
    input_summary: Optional[str] = None
    status: str
    ai_model: Optional[str] = None
    ai_model_version: Optional[str] = None
    source_entity_type: Optional[str] = None
    source_entity_id: Optional[int] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AiReportListOut(BaseModel):
    items: list[AiReportOut]
    total: int
    page: int


