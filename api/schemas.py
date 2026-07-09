"""Pydantic request/response models for the FastAPI backend."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# --- Chat ---

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Conversation id (LangGraph thread_id)")
    message: str = Field("", max_length=4096, description="Patient's latest message (Bangla)")
    clinic_slug: Optional[str] = Field(
        None, description="Which clinic this web chat is for; defaults to the seeded clinic"
    )


# --- Auth ---

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    clinic_id: Optional[int] = None


class PlatformAdminCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class PlatformAdminOut(BaseModel):
    id: int
    email: str
    role: str


# --- Clinics ---

class ClinicOut(BaseModel):
    id: int
    slug: str
    name: str
    doctor_name: str
    doctor_phone: str
    timezone: str
    availability_days_ahead: int
    status: str
    greeting_instructions: Optional[str] = None
    sms_sender_id: Optional[str] = None
    sms_templates: dict[str, str] = {}
    role: Optional[str] = None

    @field_validator("sms_templates", mode="before")
    @classmethod
    def _parse_templates(cls, v):
        # asyncpg returns a JSONB column as a JSON string; coerce to a dict.
        if isinstance(v, str):
            import json as _json
            try:
                v = _json.loads(v)
            except Exception:
                return {}
        return v if isinstance(v, dict) else {}


class ClinicUpdate(BaseModel):
    """Editable clinic settings (PATCH /clinics/me). All fields optional."""
    name: Optional[str] = Field(None, min_length=1)
    doctor_name: Optional[str] = Field(None, min_length=1)
    doctor_phone: Optional[str] = None
    availability_days_ahead: Optional[int] = Field(None, ge=1, le=60)
    timezone: Optional[str] = Field(None, min_length=1)
    greeting_instructions: Optional[str] = Field(
        None,
        description="Admin guidance for the AI-generated greeting (tone, extra info "
        "to mention); the LLM writes the actual greeting. Empty string clears it",
    )
    sms_sender_id: Optional[str] = Field(
        None, description="Per-department SMS From/sender override; empty clears it"
    )
    sms_templates: Optional[dict[str, str]] = Field(
        None, description="Editable SMS templates keyed by kind (confirmation/reminder/doctor_alert/token)"
    )


class ClinicCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$")
    name: str
    doctor_name: str = "Doctor"
    doctor_phone: str = ""
    availability_days_ahead: int = Field(7, ge=1, le=60)
    admin_email: EmailStr
    admin_password: str = Field(..., min_length=8, max_length=72)

    @field_validator("admin_password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class ChatResponse(BaseModel):
    reply: str
    phase: Optional[str] = None
    appointment_id: Optional[str] = None
    patient_name: Optional[str] = None
    done: bool = False


# --- Appointments ---

class AppointmentOut(BaseModel):
    id: str
    patient_name: str
    patient_age: int
    patient_mobile: str
    scheduled_at: datetime
    duration_mins: int
    status: str
    created_at: datetime
    serial_number: Optional[int] = None
    doctor_id: Optional[int] = None
    # Stamped when the patient replies "১" to the 24h reminder (two-way SMS).
    patient_confirmed_at: Optional[datetime] = None


class CancelRequest(BaseModel):
    status: Literal["cancelled"] = "cancelled"
    reason: Optional[str] = Field(None, max_length=500)


_LIFECYCLE_STATUSES = ("checked_in", "completed", "no_show", "cancelled")


class AppointmentStatusUpdate(BaseModel):
    status: Literal["checked_in", "completed", "no_show", "cancelled"]
    reason: Optional[str] = Field(None, max_length=500)


class AppointmentEventOut(BaseModel):
    id: int
    event_type: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    from_time: Optional[datetime] = None
    to_time: Optional[datetime] = None
    actor_user_id: Optional[int] = None
    actor_role: str = ""
    actor_email: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime


class RescheduleRequest(BaseModel):
    slot_datetime: str = Field(..., description="New slot ISO datetime, e.g. 2026-06-29T10:00:00+06:00")


# --- Schedule ---

class ScheduleRow(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, description="0=Mon … 6=Sun")
    start_time: str = Field(..., examples=["09:00"])
    end_time: str = Field(..., examples=["17:00"])
    slot_duration: int = Field(30, gt=0)


class ScheduleOut(ScheduleRow):
    id: Optional[int] = None


# --- Availability ---

class SlotOut(BaseModel):
    datetime: str
    label: str


# --- Doctors (per-clinic roster) ---

class DoctorOut(BaseModel):
    id: int
    clinic_id: int
    name: str
    specialty: str = ""
    degrees: str = ""
    description: str = ""
    phone: str = ""
    is_primary: bool = False
    has_photo: bool = False
    created_at: datetime


class DoctorCreate(BaseModel):
    name: str = Field(..., min_length=1)
    specialty: str = ""
    degrees: str = Field("", max_length=300)
    description: str = Field("", max_length=2000)
    phone: str = ""
    is_primary: bool = False


class DoctorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    specialty: Optional[str] = None
    degrees: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = Field(None, max_length=2000)
    phone: Optional[str] = None
    is_primary: Optional[bool] = None


# --- Channel stats (calls & appointments per voice number) ---

class ChannelStats(BaseModel):
    id: int
    identifier: str
    label: Optional[str] = None
    calls_received: int = 0
    appointments_taken: int = 0


# --- Channels (per-clinic number/key mappings) ---

CHANNEL_KINDS = ("web", "whatsapp", "sms", "voice", "voice_sip", "phone", "voice_ivr")


class ChannelOut(BaseModel):
    id: int
    clinic_id: Optional[int] = None
    hospital_id: Optional[int] = None
    kind: str
    identifier: str
    label: Optional[str] = None
    created_at: datetime


class ChannelCreate(BaseModel):
    kind: Literal["web", "whatsapp", "sms", "voice", "voice_sip", "phone", "voice_ivr"]
    identifier: str = Field(..., min_length=1, description="Number, WhatsApp id, DID or web key")
    label: Optional[str] = Field(None, description="Friendly name shown in the UI")
    hospital_id: Optional[int] = Field(None, description="Hospital ID for voice_ivr channels")


# --- Integrations status (platform capabilities; no secrets) ---

class IntegrationItem(BaseModel):
    key: str                  # whatsapp | sms | voice | llm | tts | stt
    name: str
    configured: bool
    detail: str = ""


class IntegrationsStatus(BaseModel):
    items: list[IntegrationItem]


# --- Conversations ---

class ConversationSummary(BaseModel):
    session_id: str
    channel: str
    turns: int
    last_text: str
    started_at: datetime
    last_at: datetime


class ConversationMessage(BaseModel):
    role: str
    text: str
    channel: str
    created_at: datetime


# --- Hospitals ---

class HospitalOut(BaseModel):
    id: int
    slug: str
    name: str
    address: str = ""
    license_number: str = ""
    timezone: str
    status: str
    created_at: datetime


class HospitalCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$")
    name: str = Field(..., min_length=1)
    address: str = ""
    license_number: str = ""
    timezone: str = "Asia/Dhaka"


class HospitalUpdate(BaseModel):
    """Editable hospital details (PATCH /hospitals/{id}). All fields optional."""
    name: Optional[str] = Field(None, min_length=1)
    address: Optional[str] = None
    license_number: Optional[str] = None
    timezone: Optional[str] = Field(None, min_length=1)
    status: Optional[str] = Field(None, pattern=r"^(active|inactive|suspended)$")


class SmsLogOut(BaseModel):
    id: int
    clinic_id: Optional[int] = None
    to_number: str
    body: str
    kind: str
    status: str
    provider: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime


class DepartmentOut(BaseModel):
    id: int
    slug: str
    name: str
    doctor_name: str = ""
    specialty_code: str = ""
    floor: str = ""
    phone_ext: str = ""


class DepartmentCreate(BaseModel):
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$")
    name: str = Field(..., min_length=1)
    doctor_name: str = "Doctor"
    specialty_code: str = ""
    floor: str = ""
    phone_ext: str = ""


# --- Patients ---

class PatientOut(BaseModel):
    id: int
    hospital_id: int
    mrn: str
    name: str
    phone: str
    age: Optional[int] = None
    gender: Optional[str] = None
    created_at: datetime


class PatientCreate(BaseModel):
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=6)
    age: Optional[int] = Field(None, ge=0, le=150)
    gender: Optional[Literal["male", "female", "other"]] = None


# --- Patient accounts (self-service portal) ---

class PatientSignup(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)
    name: str = Field(..., min_length=1)
    phone: str = Field(..., min_length=6)

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class PatientLogin(BaseModel):
    email: EmailStr
    password: str


class PasswordForgotRequest(BaseModel):
    identifier: str = Field(..., min_length=3, description="Registered email or phone")


class PasswordResetRequest(BaseModel):
    identifier: str = Field(..., min_length=3)
    code: str = Field(..., min_length=4, max_length=10)
    new_password: str = Field(..., min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class PatientTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    account_id: int


class PatientAccountOut(BaseModel):
    id: int
    email: str
    name: str
    phone: str
    created_at: datetime


class PatientAppointmentOut(BaseModel):
    id: str
    hospital_id: Optional[int] = None
    hospital_name: Optional[str] = None
    clinic_id: Optional[int] = None
    department_name: Optional[str] = None
    doctor_name: Optional[str] = None
    patient_name: str
    patient_mobile: str
    scheduled_at: datetime
    duration_mins: int
    status: str
    serial_number: Optional[int] = None
    created_at: datetime


class PatientChatRequest(BaseModel):
    session_id: str = Field(..., description="Conversation id (LangGraph thread_id)")
    message: str = Field("", max_length=4096)
    clinic_id: int = Field(..., description="Chosen department (clinic) to book in")
    doctor_id: int | None = Field(
        None, description="Pre-selected doctor from the portal wizard, if any"
    )
    # Answer to a pending confirm question (cancel/reschedule interrupt):
    # true = নিশ্চিত করুন, false = না. Omit for a normal message turn.
    resume: bool | None = None


# --- Queue / Tokens ---

class TokenOut(BaseModel):
    id: int
    appointment_id: str
    hospital_id: int
    department_id: int
    doctor_id: Optional[int] = None
    token_date: str
    token_number: int
    token_prefix: str
    status: str
    patient_name: Optional[str] = None
    patient_mobile: Optional[str] = None
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class QueueStatus(BaseModel):
    current_token: Optional[int] = None
    waiting_count: int
    tokens: list[TokenOut]


# --- Audit Log ---

class AuditEntryOut(BaseModel):
    id: int
    hospital_id: Optional[int] = None
    clinic_id: Optional[int] = None
    user_id: Optional[int] = None
    actor_role: str = ""
    actor_email: Optional[str] = None
    action: str
    entity_type: str = ""
    entity_id: Optional[str] = None
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    ip_address: Optional[str] = None
    created_at: datetime

    @field_validator("old_value", "new_value", mode="before")
    @classmethod
    def _parse_json(cls, v):
        if isinstance(v, str):
            import json as _json
            try:
                return _json.loads(v)
            except Exception:
                return None
        return v



# --- RAG Documents ---

class DocumentOut(BaseModel):
    id: int
    hospital_id: int
    filename: str
    content_type: str
    chunk_count: int
    created_at: datetime


# --- Reports / analytics ---

class PerDoctorStat(BaseModel):
    doctor_id: Optional[int] = None
    name: str
    count: int


class DailyCount(BaseModel):
    day: str
    count: int


class SmsKindStat(BaseModel):
    kind: str
    count: int


class AppointmentStats(BaseModel):
    total: int
    status_counts: dict[str, int] = {}
    completed: int
    no_show: int
    cancelled: int
    no_show_rate: float
    completion_rate: float
    per_doctor: list[PerDoctorStat] = []
    daily: list[DailyCount] = []


class SmsStats(BaseModel):
    by_status: dict[str, int] = {}
    by_kind: list[SmsKindStat] = []


class ReportSummaryOut(BaseModel):
    date_from: str
    date_to: str
    appointments: AppointmentStats
    sms: SmsStats
    channels: list[ChannelStats] = []
