from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, date
from typing import Optional, List
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import models, json, random, string
from database import engine, get_db

# ── SETUP ──────────────────────────────────────
SECRET_KEY    = "your_secure_random_key_here"
ALGORITHM     = "HS256"
pwd_context   = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

models.Base.metadata.create_all(bind=engine)
app = FastAPI(title="UniPOS Professional")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ══════════════════════════════════════════════
#  SCHEMAS
# ══════════════════════════════════════════════

class UserCreate(BaseModel):
    username: str
    password: str

class ProductCreate(BaseModel):
    name:           str
    price:          float
    stock_quantity: int

class CustomerCreate(BaseModel):
    name:  str
    phone: Optional[str] = None
    email: Optional[str] = None

class StaffCreate(BaseModel):
    name:        str
    role:        str
    phone:       Optional[str]  = None
    email:       Optional[str]  = None
    is_active:   Optional[bool] = True
    specialties: Optional[str]  = None

class ServiceCreate(BaseModel):
    name:        str
    description: Optional[str]  = None
    duration:    int
    price:       float
    category:    Optional[str]  = None
    is_active:   Optional[bool] = True

class SaleCreate(BaseModel):
    quantity:    int
    customer_id: Optional[int] = None
    product_id:  Optional[int] = None
    service_id:  Optional[int] = None

class AppointmentCreate(BaseModel):
    service_ids:  List[int]
    scheduled_at: str                    # "YYYY-MM-DD HH:MM"
    customer_id:  Optional[int]  = None
    staff_id:     Optional[int]  = None
    notes:        Optional[str]  = None
    booking_type: Optional[str]  = "online"
    duration:     Optional[int]  = None  # admin override
    guest_name:   Optional[str]  = None
    guest_phone:  Optional[str]  = None
    guest_email:  Optional[str]  = None

class AppointmentUpdate(BaseModel):
    staff_id:     Optional[int] = None
    status:       Optional[str] = None
    notes:        Optional[str] = None
    duration:     Optional[int] = None
    scheduled_at: Optional[str] = None

class AttendanceCreate(BaseModel):
    staff_id:   int
    date:       str    # "YYYY-MM-DD"
    is_present: bool

class StaffScheduleCreate(BaseModel):
    staff_id:    int
    day_of_week: int   # 0=Mon … 6=Sun
    is_working:  bool

class LeaveCreate(BaseModel):
    staff_id:   int
    leave_date: str    # "YYYY-MM-DD"
    reason:     Optional[str] = None

class CustomerNoteCreate(BaseModel):
    note:     str
    staff_id: int

class SalonSettingsUpdate(BaseModel):
    slot_buffer:       Optional[int] = None
    specialty_mapping: Optional[str] = None

# ── Day schedule schemas ──
class DayScheduleUpdate(BaseModel):
    day_of_week: int
    is_open:     bool
    open_time:   Optional[str] = None   # "HH:MM"
    close_time:  Optional[str] = None   # "HH:MM"

class WeeklyScheduleUpdate(BaseModel):
    days: List[DayScheduleUpdate]

# ── Special day schemas ──
class SpecialDayCreate(BaseModel):
    date:       str              # "YYYY-MM-DD"
    is_open:    bool
    open_time:  Optional[str]  = None
    close_time: Optional[str]  = None
    note:       Optional[str]  = None
    staff_ids:  Optional[List[int]] = []   # staff working this day

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(models.User).filter(
        models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def generate_reference() -> str:
    chars = string.ascii_uppercase + string.digits
    return "BK-" + "".join(random.choices(chars, k=5))

def get_or_create_settings(db: Session) -> models.SalonSettings:
    s = db.query(models.SalonSettings).first()
    if not s:
        s = models.SalonSettings()
        db.add(s); db.commit(); db.refresh(s)
    return s

def get_day_schedule(db: Session) -> dict:
    """
    Returns dict keyed by day_of_week (0-6).
    Falls back to sensible defaults if not configured.
    """
    rows = db.query(models.DaySchedule).all()
    defaults = {
        0: {"is_open": True,  "open_time": "09:00", "close_time": "17:00"},
        1: {"is_open": True,  "open_time": "09:00", "close_time": "17:00"},
        2: {"is_open": True,  "open_time": "09:00", "close_time": "17:00"},
        3: {"is_open": True,  "open_time": "09:00", "close_time": "17:00"},
        4: {"is_open": True,  "open_time": "09:00", "close_time": "17:00"},
        5: {"is_open": True,  "open_time": "09:00", "close_time": "17:00"},
        6: {"is_open": False, "open_time": None,    "close_time": None   },
    }
    for r in rows:
        defaults[r.day_of_week] = {
            "is_open":    r.is_open,
            "open_time":  r.open_time,
            "close_time": r.close_time,
        }
    return defaults

def get_hours_for_date(
    check_date: date, db: Session
) -> dict:
    """
    Returns effective hours for a specific date.
    Special day overrides weekly schedule.
    Returns: {is_open, open_time, close_time}
    """
    # Check special day first
    special = db.query(models.SpecialDay).filter(
        func.date(models.SpecialDay.date) == check_date
    ).first()
    if special:
        return {
            "is_open":    special.is_open,
            "open_time":  special.open_time,
            "close_time": special.close_time,
            "is_special": True,
        }
    # Fall back to weekly schedule
    # JS getDay: 0=Sun → our system: 6=Sun
    js_day  = check_date.weekday()  # 0=Mon already in Python
    sched   = get_day_schedule(db)
    day_cfg = sched.get(js_day, {"is_open": False})
    return {
        "is_open":    day_cfg["is_open"],
        "open_time":  day_cfg.get("open_time"),
        "close_time": day_cfg.get("close_time"),
        "is_special": False,
    }

def staff_can_do_service(
    staff: models.Staff,
    service_categories: List[str],
    specialty_mapping: dict
) -> bool:
    if staff.specialties == "ALL" or staff.role == "All-rounder":
        return True
    staff_cats_str = specialty_mapping.get(staff.role, "")
    if staff_cats_str == "ALL":
        return True
    if not staff_cats_str:
        return False
    allowed = [c.strip() for c in staff_cats_str.split(",")]
    return all(cat in allowed for cat in service_categories)

def is_staff_available(
    staff_id: int,
    scheduled_at: datetime,
    duration: int,
    db: Session,
    exclude_id: int = None
) -> bool:
    slot_end = scheduled_at + timedelta(minutes=duration)
    q = db.query(models.Appointment).filter(
        models.Appointment.staff_id == staff_id,
        models.Appointment.status.notin_(["cancelled", "completed"])
    )
    if exclude_id:
        q = q.filter(models.Appointment.id != exclude_id)
    for appt in q.all():
        appt_end = appt.scheduled_at + timedelta(minutes=appt.duration)
        if scheduled_at < appt_end and slot_end > appt.scheduled_at:
            return False
    return True

def is_staff_on_leave(staff_id: int, check_date: date, db: Session) -> bool:
    for l in db.query(models.LeaveRequest).filter(
        models.LeaveRequest.staff_id == staff_id
    ).all():
        if l.leave_date.date() == check_date:
            return True
    return False

def is_staff_scheduled(
    staff_id: int, check_date: date, db: Session
) -> bool:
    day = check_date.weekday()
    s = db.query(models.StaffSchedule).filter(
        models.StaffSchedule.staff_id    == staff_id,
        models.StaffSchedule.day_of_week == day
    ).first()
    return s.is_working if s else True

def check_attendance(
    staff_id: int, check_date: date, db: Session
) -> bool:
    for r in db.query(models.Attendance).filter(
        models.Attendance.staff_id == staff_id
    ).all():
        if r.date.date() == check_date:
            return r.is_present
    return True

def calculate_duration(
    service_ids: List[int], db: Session, buffer: int = 15
) -> int:
    total = sum(
        s.duration
        for sid in service_ids
        for s in [db.query(models.Service).filter(
            models.Service.id == sid).first()]
        if s
    )
    return total + buffer

def _format_appointment(a: models.Appointment) -> dict:
    customer_name = (
        a.customer.name if a.customer
        else a.guest_name or "Walk-in"
    )
    return {
        "id":           a.id,
        "reference":    a.reference,
        "customer":     customer_name,
        "customer_id":  a.customer_id,
        "guest_name":   a.guest_name,
        "guest_phone":  a.guest_phone,
        "guest_email":  a.guest_email,
        "staff":        a.staff.name if a.staff else "Unassigned",
        "staff_id":     a.staff_id,
        "services":     [
            {"id": s.id, "name": s.name, "duration": s.duration}
            for s in a.services
        ],
        "scheduled_at": a.scheduled_at.strftime("%Y-%m-%d %H:%M")
                        if a.scheduled_at else "",
        "duration":     a.duration,
        "status":       a.status,
        "booking_type": a.booking_type,
        "notes":        a.notes or "",
        "created_at":   a.created_at.strftime("%Y-%m-%d %H:%M")
                        if a.created_at else "",
    }

# ══════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════

@app.post("/auth/signup", tags=["Auth"])
def signup(user: UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(
        models.User.username == user.username
    ).first():
        raise HTTPException(400, "Username already registered")
    db.add(models.User(
        username=user.username,
        hashed_password=pwd_context.hash(user.password)
    ))
    db.commit()
    return {"message": "User created successfully"}

@app.post("/auth/login", tags=["Auth"])
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.username == form_data.username
    ).first()
    if not user or not pwd_context.verify(
        form_data.password, user.hashed_password
    ):
        raise HTTPException(401, "Invalid credentials")
    token = jwt.encode(
        {"sub": user.username,
         "exp": datetime.utcnow() + timedelta(hours=24)},
        SECRET_KEY, algorithm=ALGORITHM
    )
    return {"access_token": token, "token_type": "bearer"}

@app.put("/auth/change-password", tags=["Auth"])
def change_password(
    user: UserCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    db_user = db.query(models.User).filter(
        models.User.username == user.username
    ).first()
    if not db_user:
        raise HTTPException(404, "User not found")
    if db_user.username != current_user.username:
        raise HTTPException(403, "You can only change your own password")
    db_user.hashed_password = pwd_context.hash(user.password)
    db.commit()
    return {"message": "Password updated successfully"}

# ══════════════════════════════════════════════
#  INVENTORY
# ══════════════════════════════════════════════

@app.get("/inventory", tags=["Inventory"])
def list_inventory(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return db.query(models.Product).all()

@app.post("/products", tags=["Inventory"])
def create_product(
    product: ProductCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    p = models.Product(**product.dict())
    db.add(p); db.commit(); db.refresh(p)
    return p

@app.put("/products/{pid}", tags=["Inventory"])
def update_product(
    pid: int, product: ProductCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    p = db.query(models.Product).filter(
        models.Product.id == pid).first()
    if not p:
        raise HTTPException(404, "Product not found")
    for k, v in product.dict().items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return p

@app.delete("/products/{pid}", tags=["Inventory"])
def delete_product(
    pid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    p = db.query(models.Product).filter(
        models.Product.id == pid).first()
    if not p:
        raise HTTPException(404, "Product not found")
    if db.query(models.Sale).filter(
        models.Sale.product_id == pid
    ).count() > 0:
        p.is_active = False; db.commit()
        return {"message": "Product deactivated (has sales history)"}
    db.delete(p); db.commit()
    return {"message": "Product deleted"}

# ══════════════════════════════════════════════
#  CUSTOMERS
# ══════════════════════════════════════════════

@app.get("/customers", tags=["Customers"])
def list_customers(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return db.query(models.Customer).all()

@app.post("/customers", tags=["Customers"])
def create_customer(
    customer: CustomerCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    c = models.Customer(**customer.dict())
    db.add(c); db.commit(); db.refresh(c)
    return c

@app.delete("/customers/{cid}", tags=["Customers"])
def delete_customer(
    cid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    c = db.query(models.Customer).filter(
        models.Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "Customer not found")
    if db.query(models.Sale).filter(
        models.Sale.customer_id == cid
    ).count() > 0:
        return {"message": "Customer kept (has sales history)"}
    db.delete(c); db.commit()
    return {"message": "Customer deleted"}

# ── Customer profile endpoints ──

@app.get("/customers/{cid}/history", tags=["Customers"])
def customer_history(
    cid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    appts = db.query(models.Appointment).filter(
        models.Appointment.customer_id == cid
    ).order_by(models.Appointment.scheduled_at.desc()).all()
    return [
        {
            "id":          a.id,
            "reference":   a.reference,
            "date":        a.scheduled_at.strftime("%Y-%m-%d")
                           if a.scheduled_at else "",
            "scheduled_at": a.scheduled_at.strftime("%Y-%m-%d %H:%M")
                            if a.scheduled_at else "",
            "services":    ", ".join(s.name for s in a.services),
            "staff":       a.staff.name if a.staff else "—",
            "status":      a.status,
            "duration":    a.duration,
        }
        for a in appts
    ]

@app.get("/customers/{cid}/stats", tags=["Customers"])
def customer_stats(
    cid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    c = db.query(models.Customer).filter(
        models.Customer.id == cid).first()
    if not c:
        raise HTTPException(404, "Customer not found")

    appts = db.query(models.Appointment).filter(
        models.Appointment.customer_id == cid
    ).all()
    completed = [a for a in appts if a.status == "completed"]
    sales = db.query(models.Sale).filter(
        models.Sale.customer_id == cid
    ).all()
    total_spent = sum(s.total_amount for s in sales)

    last_visit = None
    if completed:
        last_visit = max(
            a.scheduled_at for a in completed
        ).strftime("%Y-%m-%d")

    member_since = None
    if appts:
        member_since = min(
            a.created_at for a in appts
        ).strftime("%Y-%m-%d")

    return {
        "customer_id":        cid,
        "name":               c.name,
        "total_spent":        round(total_spent, 2),
        "total_visits":       len(completed),
        "total_appointments": len(appts),
        "last_visit":         last_visit,
        "member_since":       member_since,
    }

@app.get("/customers/{cid}/notes", tags=["Customers"])
def get_notes(
    cid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    notes = db.query(models.CustomerNote).filter(
        models.CustomerNote.customer_id == cid
    ).order_by(models.CustomerNote.created_at.desc()).all()
    return [
        {
            "id":         n.id,
            "note":       n.note,
            "staff":      n.staff.name if n.staff else "—",
            "staff_id":   n.staff_id,
            "created_at": n.created_at.strftime("%b %d, %Y")
                          if n.created_at else "",
        }
        for n in notes
    ]

@app.post("/customers/{cid}/notes", tags=["Customers"])
def add_note(
    cid: int,
    note: CustomerNoteCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    n = models.CustomerNote(
        customer_id=cid,
        staff_id=note.staff_id,
        note=note.note
    )
    db.add(n); db.commit(); db.refresh(n)
    return {
        "id":         n.id,
        "note":       n.note,
        "staff":      n.staff.name if n.staff else "—",
        "created_at": n.created_at.strftime("%b %d, %Y"),
    }

@app.delete("/customers/{cid}/notes/{nid}", tags=["Customers"])
def delete_note(
    cid: int, nid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    n = db.query(models.CustomerNote).filter(
        models.CustomerNote.id          == nid,
        models.CustomerNote.customer_id == cid
    ).first()
    if not n:
        raise HTTPException(404, "Note not found")
    db.delete(n); db.commit()
    return {"message": "Note deleted"}

# ══════════════════════════════════════════════
#  STAFF
# ══════════════════════════════════════════════

def _fmt_staff(s: models.Staff) -> dict:
    return {
        "id":          s.id,
        "name":        s.name,
        "role":        s.role,
        "phone":       s.phone  or "",
        "email":       s.email  or "",
        "is_active":   s.is_active,
        "specialties": s.specialties or "ALL",
    }

@app.get("/staff", tags=["Staff"])
def list_staff(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return [_fmt_staff(s) for s in db.query(models.Staff).all()]

@app.post("/staff", tags=["Staff"])
def create_staff(
    staff: StaffCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    data = staff.dict()
    if not data.get("specialties"):
        settings = get_or_create_settings(db)
        mapping  = json.loads(settings.specialty_mapping)
        data["specialties"] = mapping.get(staff.role, "")
    s = models.Staff(**data)
    db.add(s); db.commit(); db.refresh(s)
    return _fmt_staff(s)

@app.put("/staff/{sid}", tags=["Staff"])
def update_staff(
    sid: int, staff: StaffCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = db.query(models.Staff).filter(
        models.Staff.id == sid).first()
    if not s:
        raise HTTPException(404, "Staff not found")
    data = staff.dict()
    if not data.get("specialties"):
        settings = get_or_create_settings(db)
        mapping  = json.loads(settings.specialty_mapping)
        data["specialties"] = mapping.get(staff.role, "")
    for k, v in data.items():
        setattr(s, k, v)
    db.commit(); db.refresh(s)
    return _fmt_staff(s)

@app.delete("/staff/{sid}", tags=["Staff"])
def delete_staff(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = db.query(models.Staff).filter(
        models.Staff.id == sid).first()
    if not s:
        raise HTTPException(404, "Staff not found")
    db.delete(s); db.commit()
    return {"message": "Staff deleted"}

@app.patch("/staff/{sid}/toggle", tags=["Staff"])
def toggle_staff(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = db.query(models.Staff).filter(
        models.Staff.id == sid).first()
    if not s:
        raise HTTPException(404, "Staff not found")
    s.is_active = not s.is_active
    db.commit()
    return {"id": s.id, "is_active": s.is_active}

# ── Staff schedule ──

@app.get("/staff/{sid}/schedule", tags=["Schedule"])
def get_staff_schedule(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    rows = db.query(models.StaffSchedule).filter(
        models.StaffSchedule.staff_id == sid
    ).all()
    names = ["Monday","Tuesday","Wednesday",
             "Thursday","Friday","Saturday","Sunday"]
    return [
        {
            "id":          r.id,
            "staff_id":    r.staff_id,
            "day_of_week": r.day_of_week,
            "day_name":    names[r.day_of_week],
            "is_working":  r.is_working,
        }
        for r in rows
    ]

@app.post("/staff/{sid}/schedule", tags=["Schedule"])
def set_staff_schedule(
    sid: int,
    schedule: StaffScheduleCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    existing = db.query(models.StaffSchedule).filter(
        models.StaffSchedule.staff_id    == sid,
        models.StaffSchedule.day_of_week == schedule.day_of_week
    ).first()
    if existing:
        existing.is_working = schedule.is_working
        db.commit()
        return {"message": "Schedule updated",
                "is_working": existing.is_working}
    db.add(models.StaffSchedule(
        staff_id=sid,
        day_of_week=schedule.day_of_week,
        is_working=schedule.is_working
    ))
    db.commit()
    return {"message": "Schedule set",
            "is_working": schedule.is_working}

# ══════════════════════════════════════════════
#  SERVICES
# ══════════════════════════════════════════════

def _fmt_svc(s: models.Service) -> dict:
    return {
        "id":          s.id,
        "name":        s.name,
        "description": s.description or "",
        "duration":    s.duration,
        "price":       s.price,
        "category":    s.category or "General",
        "is_active":   s.is_active,
    }

@app.get("/services", tags=["Services"])
def list_services(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return [_fmt_svc(s) for s in db.query(models.Service).all()]

@app.post("/services", tags=["Services"])
def create_service(
    service: ServiceCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = models.Service(**service.dict())
    db.add(s); db.commit(); db.refresh(s)
    return _fmt_svc(s)

@app.put("/services/{sid}", tags=["Services"])
def update_service(
    sid: int, service: ServiceCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = db.query(models.Service).filter(
        models.Service.id == sid).first()
    if not s:
        raise HTTPException(404, "Service not found")
    for k, v in service.dict().items():
        setattr(s, k, v)
    db.commit(); db.refresh(s)
    return _fmt_svc(s)

@app.delete("/services/{sid}", tags=["Services"])
def delete_service(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = db.query(models.Service).filter(
        models.Service.id == sid).first()
    if not s:
        raise HTTPException(404, "Service not found")
    from sqlalchemy import text
    count = db.execute(
        text("SELECT COUNT(*) FROM appointment_services WHERE service_id=:sid"),
        {"sid": sid}
    ).scalar()
    if count > 0:
        s.is_active = False; db.commit()
        return {"message": "Service deactivated (has appointments)"}
    db.delete(s); db.commit()
    return {"message": "Service deleted"}

@app.patch("/services/{sid}/toggle", tags=["Services"])
def toggle_service(
    sid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = db.query(models.Service).filter(
        models.Service.id == sid).first()
    if not s:
        raise HTTPException(404, "Service not found")
    s.is_active = not s.is_active
    db.commit()
    return {"id": s.id, "is_active": s.is_active}

# ══════════════════════════════════════════════
#  SALES
# ══════════════════════════════════════════════

@app.post("/sales", tags=["Sales"])
def make_sale(
    sale: SaleCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    total = 0
    if sale.product_id:
        p = db.query(models.Product).filter(
            models.Product.id == sale.product_id).first()
        if not p:
            raise HTTPException(404, "Product not found")
        if p.stock_quantity < sale.quantity:
            raise HTTPException(400,
                f"Insufficient stock. Available: {p.stock_quantity}")
        total = p.price * sale.quantity
        p.stock_quantity -= sale.quantity
    elif sale.service_id:
        s = db.query(models.Service).filter(
            models.Service.id == sale.service_id).first()
        if not s:
            raise HTTPException(404, "Service not found")
        if not s.is_active:
            raise HTTPException(400, "Service is not active")
        total = s.price * sale.quantity
    else:
        raise HTTPException(400, "Provide product_id or service_id")
    n = models.Sale(
        product_id=sale.product_id, service_id=sale.service_id,
        customer_id=sale.customer_id, quantity=sale.quantity,
        total_amount=total
    )
    db.add(n); db.commit(); db.refresh(n)
    return {"message": "Sale successful",
            "total_amount": total, "sale_id": n.id}

@app.get("/sales/history", tags=["Sales"])
def get_sales_history(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    sales = db.query(models.Sale).order_by(
        models.Sale.timestamp.desc()).all()
    return [
        {
            "id":       s.id,
            "item":     (s.product.name if s.product
                         else s.service.name if s.service
                         else "Unknown"),
            "type":     "Product" if s.product_id else "Service",
            "customer": s.customer.name if s.customer else "Walk-in",
            "total":    s.total_amount,
            "date":     s.timestamp.strftime("%Y-%m-%d %H:%M")
                        if s.timestamp else "",
        }
        for s in sales
    ]

# ══════════════════════════════════════════════
#  APPOINTMENTS
# ══════════════════════════════════════════════

@app.get("/appointments", tags=["Appointments"])
def list_appointments(
    date_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.Appointment)
    if date_filter:
        try:
            fd = datetime.strptime(date_filter, "%Y-%m-%d").date()
            q  = q.filter(
                func.date(models.Appointment.scheduled_at) == fd)
        except ValueError:
            raise HTTPException(400, "Use YYYY-MM-DD")
    return [_format_appointment(a)
            for a in q.order_by(
                models.Appointment.scheduled_at).all()]

@app.get("/appointments/today", tags=["Appointments"])
def todays_appointments(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    today = datetime.utcnow().date()
    appts = db.query(models.Appointment).filter(
        func.date(models.Appointment.scheduled_at) == today
    ).order_by(models.Appointment.scheduled_at).all()
    return [_format_appointment(a) for a in appts]

@app.get("/appointments/slots/available", tags=["Appointments"])
def get_available_slots(
    date: str = Query(...),
    service_ids: str = Query(...),
    db: Session = Depends(get_db)
):
    """
    Public endpoint — no auth required.
    Returns all 30-min slots for a date, marked free/taken.
    Respects special day overrides and weekly schedule.
    """
    try:
        check_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD")

    # Max 1 year ahead
    if check_date > (datetime.utcnow() + timedelta(days=365)).date():
        raise HTTPException(400, "Cannot book more than 1 year ahead")

    svc_ids = [int(x) for x in service_ids.split(",") if x.strip()]
    settings = get_or_create_settings(db)

    # Get effective hours for this date
    hours = get_hours_for_date(check_date, db)
    if not hours["is_open"]:
        return {
            "date":    date,
            "slots":   [],
            "message": "Salon is closed on this day",
        }

    total_duration = calculate_duration(svc_ids, db, settings.slot_buffer)

    # Service categories for staff matching
    categories = []
    for sid in svc_ids:
        svc = db.query(models.Service).filter(
            models.Service.id == sid).first()
        if svc and svc.category and svc.category not in categories:
            categories.append(svc.category)

    specialty_mapping = json.loads(settings.specialty_mapping)
    is_future = check_date > datetime.utcnow().date()

    # Get special day staff override if applicable
    special = db.query(models.SpecialDay).filter(
        func.date(models.SpecialDay.date) == check_date,
        models.SpecialDay.is_open == True
    ).first()

    all_staff = db.query(models.Staff).filter(
        models.Staff.is_active == True).all()

    # If special day — only selected staff are available
    if special and special.staff:
        available_staff_pool = special.staff
    else:
        available_staff_pool = all_staff

    # Parse hours
    oh, om = map(int, hours["open_time"].split(":"))
    ch, cm = map(int, hours["close_time"].split(":"))

    slots  = []
    cursor = datetime.combine(check_date,
                              datetime.min.time().replace(hour=oh, minute=om))
    close  = datetime.combine(check_date,
                              datetime.min.time().replace(hour=ch, minute=cm))

    while cursor + timedelta(minutes=total_duration) <= close:
        capable = 0
        for s in available_staff_pool:
            if not staff_can_do_service(s, categories, specialty_mapping):
                continue
            if not is_future:
                if is_staff_on_leave(s.id, check_date, db):
                    continue
                if not check_attendance(s.id, check_date, db):
                    continue
            if not is_staff_scheduled(s.id, check_date, db):
                continue
            if is_staff_available(s.id, cursor, total_duration, db):
                capable += 1
        slots.append({
            "time":            cursor.strftime("%H:%M"),
            "available":       capable > 0,
            "staff_available": capable,
        })
        cursor += timedelta(minutes=30)

    return {
        "date":           date,
        "total_duration": total_duration,
        "open_time":      hours["open_time"],
        "close_time":     hours["close_time"],
        "is_special_day": hours["is_special"],
        "slots":          slots,
    }

@app.get("/appointments/{aid}", tags=["Appointments"])
def get_appointment(
    aid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    a = db.query(models.Appointment).filter(
        models.Appointment.id == aid).first()
    if not a:
        raise HTTPException(404, "Appointment not found")
    return _format_appointment(a)

@app.post("/appointments", tags=["Appointments"])
def create_appointment(
    appt: AppointmentCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return _book_appointment(appt, db)

@app.post("/appointments/public", tags=["Appointments"])
def create_public_appointment(
    appt: AppointmentCreate,
    db: Session = Depends(get_db)
):
    return _book_appointment(appt, db)

def _book_appointment(appt: AppointmentCreate, db: Session):
    try:
        scheduled_at = datetime.strptime(
            appt.scheduled_at, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD HH:MM")

    # Max 1 year ahead
    if scheduled_at.date() > (
        datetime.utcnow() + timedelta(days=365)
    ).date():
        raise HTTPException(400, "Cannot book more than 1 year ahead")

    # Validate salon is open
    hours = get_hours_for_date(scheduled_at.date(), db)
    if not hours["is_open"]:
        raise HTTPException(400, "Salon is closed on this date")

    # Validate services
    services    = []
    categories  = []
    for sid in appt.service_ids:
        svc = db.query(models.Service).filter(
            models.Service.id == sid,
            models.Service.is_active == True
        ).first()
        if not svc:
            raise HTTPException(404, f"Service {sid} not found")
        services.append(svc)
        if svc.category and svc.category not in categories:
            categories.append(svc.category)

    settings = get_or_create_settings(db)
    total_duration = (
        appt.duration
        or calculate_duration(appt.service_ids, db, settings.slot_buffer)
    )

    # Customer lookup / creation
    customer_id = appt.customer_id
    if not customer_id and appt.guest_phone:
        existing = db.query(models.Customer).filter(
            models.Customer.phone == appt.guest_phone
        ).first()
        if not existing and appt.guest_email:
            existing = db.query(models.Customer).filter(
                models.Customer.email == appt.guest_email
            ).first()
        if existing:
            customer_id = existing.id
        else:
            nc = models.Customer(
                name=appt.guest_name or "Guest",
                phone=appt.guest_phone,
                email=appt.guest_email
            )
            db.add(nc); db.flush()
            customer_id = nc.id

    # Staff assignment
    specialty_mapping = json.loads(settings.specialty_mapping)
    check_date        = scheduled_at.date()
    is_future         = check_date > datetime.utcnow().date()

    # Check special day staff override
    special = db.query(models.SpecialDay).filter(
        func.date(models.SpecialDay.date) == check_date,
        models.SpecialDay.is_open == True
    ).first()

    staff_id = appt.staff_id
    if not staff_id:
        pool = special.staff if (special and special.staff) else \
               db.query(models.Staff).filter(
                   models.Staff.is_active == True).all()

        capable = []
        for s in pool:
            if not staff_can_do_service(
                s, categories, specialty_mapping
            ):
                continue
            if not is_future:
                if is_staff_on_leave(s.id, check_date, db):
                    continue
                if not check_attendance(s.id, check_date, db):
                    continue
            if not is_staff_scheduled(s.id, check_date, db):
                continue
            capable.append(s)

        if not capable:
            raise HTTPException(
                400, "No staff available for selected service(s)")

        assigned = next(
            (s for s in capable
             if is_staff_available(
                 s.id, scheduled_at, total_duration, db)),
            None
        )
        if not assigned:
            raise HTTPException(
                400,
                "No staff available at this time. "
                "Please choose another slot."
            )
        staff_id = assigned.id
    else:
        if not is_staff_available(
            staff_id, scheduled_at, total_duration, db
        ):
            raise HTTPException(
                400, "Selected staff is not available at this time")

    # Generate unique reference
    ref = generate_reference()
    while db.query(models.Appointment).filter(
        models.Appointment.reference == ref
    ).first():
        ref = generate_reference()

    new_appt = models.Appointment(
        customer_id=customer_id,
        staff_id=staff_id,
        notes=appt.notes,
        scheduled_at=scheduled_at,
        duration=total_duration,
        booking_type=appt.booking_type,
        status="scheduled",
        guest_name=appt.guest_name,
        guest_phone=appt.guest_phone,
        guest_email=appt.guest_email,
        reference=ref,
    )
    db.add(new_appt); db.flush()
    for svc in services:
        new_appt.services.append(svc)
    db.commit(); db.refresh(new_appt)
    return _format_appointment(new_appt)

@app.put("/appointments/{aid}", tags=["Appointments"])
def update_appointment(
    aid: int, update: AppointmentUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    a = db.query(models.Appointment).filter(
        models.Appointment.id == aid).first()
    if not a:
        raise HTTPException(404, "Appointment not found")
    if update.staff_id     is not None: a.staff_id = update.staff_id
    if update.status       is not None: a.status   = update.status
    if update.notes        is not None: a.notes    = update.notes
    if update.duration     is not None: a.duration = update.duration
    if update.scheduled_at is not None:
        try:
            a.scheduled_at = datetime.strptime(
                update.scheduled_at, "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(400, "Use YYYY-MM-DD HH:MM")
    db.commit(); db.refresh(a)
    return _format_appointment(a)

@app.delete("/appointments/{aid}", tags=["Appointments"])
def cancel_appointment(
    aid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    a = db.query(models.Appointment).filter(
        models.Appointment.id == aid).first()
    if not a:
        raise HTTPException(404, "Appointment not found")
    a.status = "cancelled"
    db.commit()
    return {"message": "Appointment cancelled", "reference": a.reference}

# ══════════════════════════════════════════════
#  ATTENDANCE
# ══════════════════════════════════════════════

@app.get("/attendance", tags=["Attendance"])
def get_attendance(
    date_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    q = db.query(models.Attendance)
    if date_filter:
        try:
            fd = datetime.strptime(date_filter, "%Y-%m-%d").date()
            q  = q.filter(
                func.date(models.Attendance.date) == fd)
        except ValueError:
            raise HTTPException(400, "Use YYYY-MM-DD")
    return [
        {
            "id":         r.id,
            "staff_id":   r.staff_id,
            "staff_name": r.staff.name if r.staff else "",
            "date":       r.date.strftime("%Y-%m-%d"),
            "is_present": r.is_present,
        }
        for r in q.all()
    ]

@app.post("/attendance", tags=["Attendance"])
def mark_attendance(
    record: AttendanceCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    try:
        att_date = datetime.strptime(record.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD")
    for r in db.query(models.Attendance).filter(
        models.Attendance.staff_id == record.staff_id
    ).all():
        if r.date.date() == att_date.date():
            r.is_present = record.is_present
            db.commit()
            return {"message": "Attendance updated",
                    "is_present": r.is_present}
    db.add(models.Attendance(
        staff_id=record.staff_id,
        date=att_date,
        is_present=record.is_present
    ))
    db.commit()
    return {"message": "Attendance marked",
            "is_present": record.is_present}

# ══════════════════════════════════════════════
#  LEAVE REQUESTS
# ══════════════════════════════════════════════

@app.get("/leaves", tags=["Leave"])
def list_leaves(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    return [
        {
            "id":         l.id,
            "staff_id":   l.staff_id,
            "staff_name": l.staff.name if l.staff else "",
            "leave_date": l.leave_date.strftime("%Y-%m-%d"),
            "reason":     l.reason or "",
        }
        for l in db.query(models.LeaveRequest).all()
    ]

@app.post("/leaves", tags=["Leave"])
def add_leave(
    leave: LeaveCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    try:
        ld = datetime.strptime(leave.leave_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD")
    affected = db.query(models.Appointment).filter(
        models.Appointment.staff_id == leave.staff_id,
        func.date(models.Appointment.scheduled_at) == ld.date(),
        models.Appointment.status == "scheduled"
    ).all()
    db.add(models.LeaveRequest(
        staff_id=leave.staff_id,
        leave_date=ld, reason=leave.reason
    ))
    db.commit()
    return {
        "message":                "Leave recorded",
        "affected_appointments":  len(affected),
        "warning": (
            f"{len(affected)} appointment(s) affected. "
            "Please reassign or notify customers."
            if affected else None
        ),
        "appointments": [
            {
                "id":        a.id,
                "reference": a.reference,
                "customer":  (a.customer.name if a.customer
                              else a.guest_name or "Guest"),
                "time":      a.scheduled_at.strftime("%H:%M"),
            }
            for a in affected
        ],
    }

@app.delete("/leaves/{lid}", tags=["Leave"])
def remove_leave(
    lid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    l = db.query(models.LeaveRequest).filter(
        models.LeaveRequest.id == lid).first()
    if not l:
        raise HTTPException(404, "Leave not found")
    db.delete(l); db.commit()
    return {"message": "Leave removed"}

# ══════════════════════════════════════════════
#  SALON SCHEDULE — WEEKLY
# ══════════════════════════════════════════════

@app.get("/schedule/salon", tags=["Salon Schedule"])
def get_salon_schedule(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    sched = get_day_schedule(db)
    names = ["Monday","Tuesday","Wednesday",
             "Thursday","Friday","Saturday","Sunday"]
    return [
        {
            "day_of_week": d,
            "day_name":    names[d],
            "is_open":     sched[d]["is_open"],
            "open_time":   sched[d]["open_time"],
            "close_time":  sched[d]["close_time"],
        }
        for d in range(7)
    ]

@app.post("/schedule/salon", tags=["Salon Schedule"])
def save_salon_schedule(
    payload: WeeklyScheduleUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    for day in payload.days:
        existing = db.query(models.DaySchedule).filter(
            models.DaySchedule.day_of_week == day.day_of_week
        ).first()
        if existing:
            existing.is_open    = day.is_open
            existing.open_time  = day.open_time  if day.is_open else None
            existing.close_time = day.close_time if day.is_open else None
        else:
            db.add(models.DaySchedule(
                day_of_week=day.day_of_week,
                is_open=day.is_open,
                open_time=day.open_time   if day.is_open else None,
                close_time=day.close_time if day.is_open else None,
            ))
    db.commit()
    return {"message": "Weekly schedule saved"}

# ══════════════════════════════════════════════
#  SALON SCHEDULE — SPECIAL DAYS
# ══════════════════════════════════════════════

@app.get("/schedule/special", tags=["Salon Schedule"])
def get_special_days(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    specials = db.query(models.SpecialDay).order_by(
        models.SpecialDay.date).all()
    return [_fmt_special(s) for s in specials]

@app.post("/schedule/special", tags=["Salon Schedule"])
def add_special_day(
    payload: SpecialDayCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    try:
        sd = datetime.strptime(payload.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Use YYYY-MM-DD")

    # Max 1 year ahead
    if sd.date() > (datetime.utcnow() + timedelta(days=365)).date():
        raise HTTPException(
            400, "Cannot add special day more than 1 year ahead")

    # Check duplicate
    if db.query(models.SpecialDay).filter(
        func.date(models.SpecialDay.date) == sd.date()
    ).first():
        raise HTTPException(
            400, "A special day already exists for this date")

    # Validate open time if is_open
    if payload.is_open and (
        not payload.open_time or not payload.close_time
    ):
        raise HTTPException(
            400, "open_time and close_time required when is_open=true")

    new_sd = models.SpecialDay(
        date=sd,
        is_open=payload.is_open,
        open_time=payload.open_time   if payload.is_open else None,
        close_time=payload.close_time if payload.is_open else None,
        note=payload.note,
    )
    db.add(new_sd); db.flush()

    # Attach staff
    if payload.is_open and payload.staff_ids:
        for staff_id in payload.staff_ids:
            s = db.query(models.Staff).filter(
                models.Staff.id == staff_id).first()
            if s:
                new_sd.staff.append(s)

    # Check affected appointments if closing a normally open day
    affected = []
    if not payload.is_open:
        affected = db.query(models.Appointment).filter(
            func.date(models.Appointment.scheduled_at) == sd.date(),
            models.Appointment.status == "scheduled"
        ).all()

    db.commit(); db.refresh(new_sd)
    return {
        "message":               "Special day added",
        "special_day":           _fmt_special(new_sd),
        "affected_appointments": len(affected),
        "warning": (
            f"{len(affected)} appointment(s) exist on this date."
            if affected else None
        ),
        "appointments": [
            {
                "id":        a.id,
                "reference": a.reference,
                "customer":  (a.customer.name if a.customer
                              else a.guest_name or "Guest"),
                "time":      a.scheduled_at.strftime("%H:%M"),
            }
            for a in affected
        ],
    }

@app.delete("/schedule/special/{sdid}", tags=["Salon Schedule"])
def remove_special_day(
    sdid: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    sd = db.query(models.SpecialDay).filter(
        models.SpecialDay.id == sdid).first()
    if not sd:
        raise HTTPException(404, "Special day not found")
    db.delete(sd); db.commit()
    return {"message": "Special day removed"}

def _fmt_special(s: models.SpecialDay) -> dict:
    return {
        "id":         s.id,
        "date":       s.date.strftime("%Y-%m-%d"),
        "is_open":    s.is_open,
        "open_time":  s.open_time,
        "close_time": s.close_time,
        "note":       s.note or "",
        "staff":      [
            {"id": st.id, "name": st.name}
            for st in s.staff
        ],
    }

# ══════════════════════════════════════════════
#  GENERAL SETTINGS (buffer + specialty mapping)
# ══════════════════════════════════════════════

@app.get("/settings", tags=["Settings"])
def get_settings(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = get_or_create_settings(db)
    return {
        "slot_buffer":       s.slot_buffer,
        "specialty_mapping": json.loads(s.specialty_mapping),
    }

@app.put("/settings", tags=["Settings"])
def update_settings(
    update: SalonSettingsUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    s = get_or_create_settings(db)
    if update.slot_buffer is not None:
        s.slot_buffer = update.slot_buffer
    if update.specialty_mapping is not None:
        try:
            json.loads(update.specialty_mapping)
        except Exception:
            raise HTTPException(
                400, "specialty_mapping must be valid JSON")
        s.specialty_mapping = update.specialty_mapping
    db.commit()
    return {
        "message":           "Settings updated",
        "slot_buffer":       s.slot_buffer,
        "specialty_mapping": json.loads(s.specialty_mapping),
    }

# ══════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════

@app.get("/analytics/dashboard", tags=["Reports"])
def get_dashboard(
    db: Session = Depends(get_db),
    _=Depends(get_current_user)
):
    revenue = db.query(
        func.sum(models.Sale.total_amount)).scalar() or 0
    sales_count = db.query(
        func.count(models.Sale.id)).scalar() or 0
    low_stock = db.query(
        func.count(models.Product.id)
    ).filter(models.Product.stock_quantity < 5).scalar() or 0
    staff_count = db.query(
        func.count(models.Staff.id)
    ).filter(models.Staff.is_active == True).scalar() or 0
    svc_count = db.query(
        func.count(models.Service.id)
    ).filter(models.Service.is_active == True).scalar() or 0

    today = datetime.utcnow().date()
    todays_appts = db.query(
        func.count(models.Appointment.id)
    ).filter(
        func.date(models.Appointment.scheduled_at) == today,
        models.Appointment.status == "scheduled"
    ).scalar() or 0

    recent = db.query(models.Sale).order_by(
        models.Sale.timestamp.desc()).limit(5).all()

    return {
        "revenue":             round(revenue, 2),
        "sales_count":         sales_count,
        "low_stock_count":     low_stock,
        "staff_count":         staff_count,
        "service_count":       svc_count,
        "todays_appointments": todays_appts,
        "recent_sales": [
            {
                "id":       s.id,
                "item":     (s.product.name if s.product
                             else s.service.name if s.service
                             else "Unknown"),
                "type":     "Product" if s.product_id else "Service",
                "customer": s.customer.name if s.customer else "Walk-in",
                "total":    s.total_amount,
            }
            for s in recent
        ],
    }