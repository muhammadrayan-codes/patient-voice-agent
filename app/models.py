"""
Patient data model.

Design notes:
- Uses SQLModel (Pydantic v2 + SQLAlchemy) so the same class defines both
  the DB table and the request/response validation schema, cutting
  boilerplate in half — important under a 3-hour time budget.
- UUID primary key generated server-side (patient_id), never client-supplied.
- Soft delete via `deleted_at` per spec (DELETE never removes the row).
- `created_at` / `updated_at` are set/refreshed server-side, never trusted
  from client input.
"""
import re
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import field_validator
from sqlmodel import SQLModel, Field

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}

NAME_RE = re.compile(r"^[A-Za-z' -]{1,50}$")
PHONE_RE = re.compile(r"^\d{10}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PatientBase(SQLModel):
    first_name: str = Field(max_length=50)
    last_name: str = Field(max_length=50)
    date_of_birth: date
    sex: str  # Male | Female | Other | Decline to Answer
    phone_number: str = Field(max_length=10)
    email: Optional[str] = None
    address_line_1: str
    address_line_2: Optional[str] = None
    city: str = Field(max_length=100)
    state: str = Field(max_length=2)
    zip_code: str
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    # ---- validators shared by create + update payloads ----
    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("Name must be 1-50 alphabetic characters (hyphens/apostrophes allowed)")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v

    @field_validator("sex")
    @classmethod
    def validate_sex(cls, v: str) -> str:
        allowed = {"Male", "Female", "Other", "Decline to Answer"}
        if v not in allowed:
            raise ValueError(f"sex must be one of {allowed}")
        return v

    @field_validator("phone_number", "emergency_contact_phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        digits = re.sub(r"\D", "", v)
        if not PHONE_RE.match(digits):
            raise ValueError("Phone number must be a valid 10-digit US number")
        return digits

    @field_validator("state")
    @classmethod
    def validate_state(cls, v: str) -> str:
        v = v.upper()
        if v not in US_STATES:
            raise ValueError("state must be a valid 2-letter US state abbreviation")
        return v

    @field_validator("zip_code")
    @classmethod
    def validate_zip(cls, v: str) -> str:
        if not ZIP_RE.match(v):
            raise ValueError("zip_code must be 5 digits or ZIP+4 format")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("email must be a valid email address")
        return v


class Patient(PatientBase, table=True):
    """DB table. Inherits validated fields from PatientBase."""
    patient_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)


class PatientCreate(PatientBase):
    """POST /patients body — all required fields per spec must be present."""
    pass


class PatientUpdate(SQLModel):
    """PUT /patients/:id body — every field optional for partial updates."""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    # Re-use the same validation logic for any field that IS provided
    _validate_name = field_validator("first_name", "last_name")(PatientBase.validate_name.__func__)
    _validate_dob = field_validator("date_of_birth")(PatientBase.validate_dob.__func__)
    _validate_sex = field_validator("sex")(PatientBase.validate_sex.__func__)
    _validate_phone = field_validator("phone_number", "emergency_contact_phone")(PatientBase.validate_phone.__func__)
    _validate_state = field_validator("state")(PatientBase.validate_state.__func__)
    _validate_zip = field_validator("zip_code")(PatientBase.validate_zip.__func__)
    _validate_email = field_validator("email")(PatientBase.validate_email.__func__)


class PatientRead(PatientBase):
    patient_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
