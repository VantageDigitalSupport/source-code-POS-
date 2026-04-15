from sqlalchemy import (Column, Integer, String, Float,
                        ForeignKey, DateTime, Boolean, Table)
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

# ══════════════════════════════════════════════
#  JUNCTION TABLES
# ══════════════════════════════════════════════

# Appointment ↔ Service (many-to-many)
appointment_services = Table(
    "appointment_services", Base.metadata,
    Column("appointment_id", Integer,
           ForeignKey("appointments.id"), primary_key=True),
    Column("service_id", Integer,
           ForeignKey("services.id"),    primary_key=True),
)

# SpecialDay ↔ Staff (many-to-many)
special_day_staff = Table(
    "special_day_staff", Base.metadata,
    Column("special_day_id", Integer,
           ForeignKey("special_days.id"), primary_key=True),
    Column("staff_id", Integer,
           ForeignKey("staff.id"),        primary_key=True),
)

# ══════════════════════════════════════════════
#  CORE MODELS
# ══════════════════════════════════════════════

class User(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    username        = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Product(Base):
    __tablename__ = "products"
    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String, index=True)
    price          = Column(Float)
    stock_quantity = Column(Integer)
    is_active      = Column(Boolean, default=True)

class Customer(Base):
    __tablename__ = "customers"
    id    = Column(Integer, primary_key=True, index=True)
    name  = Column(String, index=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

class Staff(Base):
    __tablename__ = "staff"
    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String, index=True)
    role         = Column(String)
    phone        = Column(String, nullable=True)
    email        = Column(String, nullable=True)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    # Comma-separated categories e.g. "Hair,Coloring" or "ALL"
    specialties  = Column(String, default="ALL")

    appointments   = relationship(
        "Appointment", back_populates="staff",
        cascade="all, delete-orphan")
    attendances    = relationship(
        "Attendance", back_populates="staff",
        cascade="all, delete-orphan")
    schedules      = relationship(
        "StaffSchedule", back_populates="staff",
        cascade="all, delete-orphan")
    leave_requests = relationship(
        "LeaveRequest", back_populates="staff",
        cascade="all, delete-orphan")
    notes          = relationship(
        "CustomerNote", back_populates="staff",
        cascade="all, delete-orphan")

class Service(Base):
    __tablename__ = "services"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String, index=True)
    description = Column(String, nullable=True)
    duration    = Column(Integer)   # minutes
    price       = Column(Float)
    category    = Column(String, nullable=True)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

class Sale(Base):
    __tablename__ = "sales"
    id           = Column(Integer, primary_key=True, index=True)
    product_id   = Column(Integer, ForeignKey("products.id"),  nullable=True)
    service_id   = Column(Integer, ForeignKey("services.id"),  nullable=True)
    customer_id  = Column(Integer, ForeignKey("customers.id"), nullable=True)
    quantity     = Column(Integer)
    total_amount = Column(Float)
    timestamp    = Column(DateTime, default=datetime.utcnow)
    product  = relationship("Product")
    customer = relationship("Customer")
    service  = relationship("Service")

# ══════════════════════════════════════════════
#  APPOINTMENTS
# ══════════════════════════════════════════════

class Appointment(Base):
    __tablename__ = "appointments"
    id           = Column(Integer, primary_key=True, index=True)
    customer_id  = Column(Integer, ForeignKey("customers.id"), nullable=True)
    staff_id     = Column(Integer, ForeignKey("staff.id"),     nullable=True)
    notes        = Column(String, nullable=True)
    scheduled_at = Column(DateTime)
    duration     = Column(Integer)   # total minutes including buffer
    booking_type = Column(String, default="online")  # online | walkin
    created_at   = Column(DateTime, default=datetime.utcnow)
    status       = Column(String, default="scheduled")
    # scheduled | confirmed | completed | cancelled | postponed
    guest_name   = Column(String, nullable=True)
    guest_phone  = Column(String, nullable=True)
    guest_email  = Column(String, nullable=True)
    reference    = Column(String, nullable=True, unique=True)

    staff    = relationship("Staff", back_populates="appointments")
    customer = relationship("Customer")
    services = relationship(
        "Service",
        secondary=appointment_services,
        backref="appointments"
    )

# ══════════════════════════════════════════════
#  ATTENDANCE & SCHEDULING
# ══════════════════════════════════════════════

class Attendance(Base):
    __tablename__ = "attendance"
    id         = Column(Integer, primary_key=True, index=True)
    staff_id   = Column(Integer, ForeignKey("staff.id"))
    date       = Column(DateTime)
    is_present = Column(Boolean, default=True)
    marked_at  = Column(DateTime, default=datetime.utcnow)
    staff      = relationship("Staff", back_populates="attendances")

class StaffSchedule(Base):
    __tablename__ = "staff_schedules"
    id          = Column(Integer, primary_key=True, index=True)
    staff_id    = Column(Integer, ForeignKey("staff.id"))
    day_of_week = Column(Integer)   # 0=Mon … 6=Sun
    is_working  = Column(Boolean, default=True)
    staff       = relationship("Staff", back_populates="schedules")

class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    id         = Column(Integer, primary_key=True, index=True)
    staff_id   = Column(Integer, ForeignKey("staff.id"))
    leave_date = Column(DateTime)
    reason     = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    staff      = relationship("Staff", back_populates="leave_requests")

# ══════════════════════════════════════════════
#  SALON SCHEDULE SETTINGS
# ══════════════════════════════════════════════

class DaySchedule(Base):
    """
    Regular weekly schedule — one row per day (7 rows total).
    day_of_week: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    """
    __tablename__ = "day_schedules"
    id          = Column(Integer, primary_key=True, index=True)
    day_of_week = Column(Integer, unique=True)
    is_open     = Column(Boolean, default=True)
    open_time   = Column(String,  default="09:00")   # "HH:MM"
    close_time  = Column(String,  default="17:00")   # "HH:MM"

class SpecialDay(Base):
    """
    One-off date overrides — open on normally closed day
    or closed on normally open day.
    Max 1 year ahead enforced in API layer.
    """
    __tablename__ = "special_days"
    id         = Column(Integer, primary_key=True, index=True)
    date       = Column(DateTime, unique=True)
    is_open    = Column(Boolean, default=False)
    open_time  = Column(String, nullable=True)   # only if is_open
    close_time = Column(String, nullable=True)   # only if is_open
    note       = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Staff working this special day (only relevant if is_open)
    staff = relationship(
        "Staff",
        secondary=special_day_staff,
        backref="special_days"
    )

# ══════════════════════════════════════════════
#  SALON GENERAL SETTINGS
# ══════════════════════════════════════════════

class SalonSettings(Base):
    """
    General settings — slot buffer and specialty mapping.
    open_time / close_time / working_days moved to DaySchedule.
    """
    __tablename__ = "salon_settings"
    id                = Column(Integer, primary_key=True, index=True)
    slot_buffer       = Column(Integer, default=15)
    specialty_mapping = Column(String, default=(
        '{"Stylist":"Hair,Treatment",'
        '"Barber":"Hair",'
        '"Colorist":"Coloring,Hair",'
        '"Nail Technician":"Nails",'
        '"Makeup Artist":"Makeup,Skin",'
        '"Massage Therapist":"Massage",'
        '"Receptionist":"",'
        '"Manager":"",'
        '"All-rounder":"ALL",'
        '"Other":""}'
    ))

# ══════════════════════════════════════════════
#  CUSTOMER NOTES
# ══════════════════════════════════════════════

class CustomerNote(Base):
    __tablename__ = "customer_notes"
    id          = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    staff_id    = Column(Integer, ForeignKey("staff.id"))
    note        = Column(String)
    created_at  = Column(DateTime, default=datetime.utcnow)
    staff       = relationship("Staff", back_populates="notes")
    customer    = relationship("Customer")