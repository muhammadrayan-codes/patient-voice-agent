"""
Patient Registration REST API.

Endpoints (per spec):
  GET    /patients            list, filterable by last_name / date_of_birth / phone_number
  GET    /patients/:id        fetch one
  POST   /patients            create
  PUT    /patients/:id        partial update
  DELETE /patients/:id        soft delete

  POST   /vapi/tools/create_patient        \  Vapi function-call webhooks.
  POST   /vapi/tools/lookup_patient        /  Thin wrappers around the same
                                               service logic as the REST API
                                               (single source of truth).

Envelope: every response is { "data": ..., "error": null } on success,
{ "data": null, "error": "message" } on failure, per spec.
"""
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Optional

import pathlib

from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import ValidationError
from sqlmodel import Session, select

from .database import init_db, get_session
from .models import Patient, PatientCreate, PatientUpdate, PatientRead

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("patient-api")

app = FastAPI(title="Patient Registration API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for assessment scope; lock down in real prod
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    logger.info("Database initialized.")


def envelope(data=None, error=None):
    return {"data": data, "error": error}


def to_read(p: Patient) -> dict:
    return PatientRead.model_validate(p).model_dump(mode="json")


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    # Body/query param validation failures (including our custom field_validators)
    # land here, not in pydantic.ValidationError, because FastAPI wraps them.
    messages = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=422, content=envelope(error="; ".join(messages)))


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Keep 404s etc. in the same { data, error } envelope instead of FastAPI's default {detail}.
    return JSONResponse(status_code=exc.status_code, content=envelope(error=exc.detail))


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/patients")
def list_patients(
    last_name: Optional[str] = Query(None),
    date_of_birth: Optional[date] = Query(None),
    phone_number: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    if last_name:
        stmt = stmt.where(Patient.last_name.ilike(last_name))
    if date_of_birth:
        stmt = stmt.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        digits = "".join(c for c in phone_number if c.isdigit())
        stmt = stmt.where(Patient.phone_number == digits)
    results = session.exec(stmt).all()
    return envelope(data=[to_read(p) for p in results])


@app.get("/patients/{patient_id}")
def get_patient(patient_id: uuid.UUID, session: Session = Depends(get_session)):
    patient = session.get(Patient, patient_id)
    if not patient or patient.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return envelope(data=to_read(patient))


@app.post("/patients", status_code=201)
def create_patient(payload: PatientCreate, session: Session = Depends(get_session)):
    patient = Patient(**payload.model_dump())
    session.add(patient)
    session.commit()
    session.refresh(patient)
    logger.info("Created patient %s %s (%s)", patient.first_name, patient.last_name, patient.patient_id)
    return envelope(data=to_read(patient))


@app.put("/patients/{patient_id}")
def update_patient(patient_id: uuid.UUID, payload: PatientUpdate, session: Session = Depends(get_session)):
    patient = session.get(Patient, patient_id)
    if not patient or patient.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Patient not found")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(patient, field, value)
    patient.updated_at = datetime.now(timezone.utc)
    session.add(patient)
    session.commit()
    session.refresh(patient)
    logger.info("Updated patient %s", patient.patient_id)
    return envelope(data=to_read(patient))


@app.delete("/patients/{patient_id}")
def delete_patient(patient_id: uuid.UUID, session: Session = Depends(get_session)):
    patient = session.get(Patient, patient_id)
    if not patient or patient.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Patient not found")
    patient.deleted_at = datetime.now(timezone.utc)
    session.add(patient)
    session.commit()
    logger.info("Soft-deleted patient %s", patient.patient_id)
    return envelope(data={"patient_id": str(patient_id), "deleted": True})


# ---------------------------------------------------------------------------
# Vapi tool-call webhooks
#
# Vapi calls these mid-conversation when the LLM decides to invoke a
# "function"/tool. Request shape follows Vapi's server-tool-call format:
# { "message": { "toolCalls": [ { "id": ..., "function": { "name", "arguments" } } ] } }
# We respond with { "results": [ { "toolCallId": ..., "result": "<string>" } ] }
# per Vapi's expected webhook response format.
# ---------------------------------------------------------------------------

def _vapi_result(tool_call_id: str, result: dict) -> dict:
    import json
    return {"toolCallId": tool_call_id, "result": json.dumps(result)}


@app.post("/vapi/tools/lookup_patient")
async def vapi_lookup_patient(request: Request, session: Session = Depends(get_session)):
    body = await request.json()
    results = []
    for call in body.get("message", {}).get("toolCalls", []):
        args = call["function"]["arguments"]
        if isinstance(args, str):
            import json
            args = json.loads(args)
        phone = "".join(c for c in str(args.get("phone_number", "")) if c.isdigit())
        stmt = select(Patient).where(Patient.phone_number == phone, Patient.deleted_at.is_(None))
        patient = session.exec(stmt).first()
        if patient:
            results.append(_vapi_result(call["id"], {
                "found": True,
                "first_name": patient.first_name,
                "last_name": patient.last_name,
                "patient_id": str(patient.patient_id),
            }))
        else:
            results.append(_vapi_result(call["id"], {"found": False}))
    return {"results": results}


@app.post("/vapi/tools/create_patient")
async def vapi_create_patient(request: Request, session: Session = Depends(get_session)):
    body = await request.json()
    results = []
    for call in body.get("message", {}).get("toolCalls", []):
        args = call["function"]["arguments"]
        if isinstance(args, str):
            import json
            args = json.loads(args)
        try:
            payload = PatientCreate(**args)
            patient = Patient(**payload.model_dump())
            session.add(patient)
            session.commit()
            session.refresh(patient)
            logger.info("VAPI created patient %s %s (%s)", patient.first_name, patient.last_name, patient.patient_id)
            results.append(_vapi_result(call["id"], {
                "success": True,
                "patient_id": str(patient.patient_id),
                "message": f"Saved successfully for {patient.first_name} {patient.last_name}.",
            }))
        except ValidationError as e:
            logger.warning("VAPI create_patient validation error: %s", e)
            results.append(_vapi_result(call["id"], {
                "success": False,
                "error": str(e),
            }))
        except Exception as e:
            session.rollback()
            logger.error("VAPI create_patient DB error: %s", e)
            results.append(_vapi_result(call["id"], {
                "success": False,
                "error": "Database write failed. Please ask the caller to try again.",
            }))
    return {"results": results}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/dashboard")
def dashboard():
    path = pathlib.Path(__file__).parent / "static" / "dashboard.html"
    return FileResponse(path)
