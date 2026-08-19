import uuid
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import appointments_collection, users_collection
from app.schemas.appointment import AppointmentCreate, AppointmentOut, AppointmentStatusUpdate

router = APIRouter(prefix="/appointments", tags=["appointments"])

# Appointments are the link that justifies a doctor calling a specific
# patient (see ws_manager.handle_message's call:invite handler, which
# checks for a "scheduled" appointment between the two before letting a
# call ring). This router only covers creating/listing/closing them --
# scheduling UI/calendar features are out of scope here.
#
# Known limitation: list_appointments below still assumes exactly two
# roles (doctor/patient) to decide which side of the record a user is on.
# That's fine while "doctor" is the only VIDEO_CALL_INITIATE-permitted
# role, but won't generalize if the role list grows (epic §6). Flagged
# rather than fixed here since the appointment model itself is a
# placeholder pending the tenant/assignment eligibility redesign (see the
# gap-analysis doc).


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(body: AppointmentCreate, current_user: dict = Depends(get_current_user)):
    if not settings.has_permission(current_user["role"], "VIDEO_CALL_INITIATE"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to schedule calls")

    patient = await users_collection.find_one({"_id": ObjectId(body.patient_id)})
    if not patient or patient["role"] != "patient":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    doc = {
        "appointment_id": str(uuid.uuid4()),
        "doctor_id": str(current_user["_id"]),
        "doctor_name": current_user["name"],
        "patient_id": body.patient_id,
        "patient_name": patient["name"],
        "scheduled_time": body.scheduled_time,
        "status": "scheduled",
        "notes": body.notes,
        "created_at": datetime.now(timezone.utc),
    }
    await appointments_collection.insert_one(doc)
    return AppointmentOut(id=doc["appointment_id"], **{k: doc[k] for k in doc if k != "appointment_id"})


@router.get("", response_model=list[AppointmentOut])
async def list_appointments(current_user: dict = Depends(get_current_user)):
    user_id = str(current_user["_id"])
    field = "doctor_id" if current_user["role"] == "doctor" else "patient_id"
    cursor = appointments_collection.find({field: user_id}).sort("scheduled_time", -1)
    results = []
    async for a in cursor:
        results.append(AppointmentOut(id=a["appointment_id"], **{k: a[k] for k in a if k not in ("_id", "appointment_id")}))
    return results


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def update_appointment_status(appointment_id: str, body: AppointmentStatusUpdate, current_user: dict = Depends(get_current_user)):
    appointment = await appointments_collection.find_one({"appointment_id": appointment_id})
    if not appointment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appointment["doctor_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the doctor on this appointment can update it")

    await appointments_collection.update_one({"appointment_id": appointment_id}, {"$set": {"status": body.status}})
    appointment["status"] = body.status
    return AppointmentOut(id=appointment["appointment_id"], **{k: appointment[k] for k in appointment if k not in ("_id", "appointment_id")})
