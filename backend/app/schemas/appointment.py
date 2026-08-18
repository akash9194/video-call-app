from datetime import datetime
from pydantic import BaseModel, Field


class AppointmentCreate(BaseModel):
    patient_id: str
    scheduled_time: datetime
    notes: str | None = Field(default=None, max_length=500)


class AppointmentOut(BaseModel):
    id: str
    doctor_id: str
    doctor_name: str
    patient_id: str
    patient_name: str
    scheduled_time: datetime
    status: str  # "scheduled" | "completed" | "cancelled"
    notes: str | None = None
    created_at: datetime


class AppointmentStatusUpdate(BaseModel):
    status: str = Field(pattern="^(completed|cancelled)$")
