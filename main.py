"""
Control de Herramientas — Backend central
==========================================
Fuente única de datos para las apps de área (Cableado / Potencia / Refrigeración)
y la App Principal (admin). Reemplaza el localStorage local por una base de datos
compartida, de modo que TODAS las tablets vean el mismo inventario en tiempo real.

Stack: FastAPI + SQLAlchemy. Funciona con PostgreSQL (producción/Railway) o con
SQLite (pruebas locales, sin configurar nada).

Variables de entorno:
  DATABASE_URL   URL de Postgres (Railway la inyecta sola al agregar el plugin).
                 Si no existe, usa SQLite local (./deposito.db).
  ADMIN_TOKEN    (opcional) Si se define, los endpoints de admin exigen el header
                 X-Admin-Token con ese valor.
"""
import os
import time
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, String, Integer, BigInteger, Text, Boolean, select, delete
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
)

# ──────────────────────────── DB SETUP ────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./deposito.db")
# Railway a veces entrega "postgres://"; SQLAlchemy necesita "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")  # None => admin abierto (igual que hoy)


class Base(DeclarativeBase):
    pass


class Tool(Base):
    __tablename__ = "deposito_tools"   # prefijo para convivir en una BD compartida
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    area: Mapped[str] = mapped_column(String(40), index=True)
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="disponible")
    used_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    checkout_time: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reserved_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    reserved_time: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "area": self.area, "image": self.image,
            "status": self.status, "usedBy": self.used_by,
            "checkoutTime": self.checkout_time, "reservedBy": self.reserved_by,
            "reservedTime": self.reserved_time, "notes": self.notes,
        }


class Log(Base):
    __tablename__ = "deposito_logs"   # prefijo para convivir en una BD compartida
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[int] = mapped_column(BigInteger)
    tool_id: Mapped[str] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(200))
    tool_area: Mapped[str] = mapped_column(String(40))
    tech_area: Mapped[str] = mapped_column(String(40))
    tecnico: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(20))  # salida | entrada | reserva
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # minutos

    def to_dict(self):
        return {
            "id": self.id, "timestamp": self.timestamp, "toolId": self.tool_id,
            "toolName": self.tool_name, "toolArea": self.tool_area,
            "techArea": self.tech_area, "tecnico": self.tecnico,
            "action": self.action, "notes": self.notes, "duration": self.duration,
        }


Base.metadata.create_all(engine)


# ──────────────────────────── HELPERS ────────────────────────────
def now_ms() -> int:
    return int(time.time() * 1000)


def add_log(s: Session, tool: Tool, tecnico: str, tech_area: str,
            action: str, notes: Optional[str], duration: Optional[int] = None):
    s.add(Log(
        timestamp=now_ms(), tool_id=tool.id, tool_name=tool.name,
        tool_area=tool.area, tech_area=tech_area or tool.area,
        tecnico=tecnico, action=action, notes=notes, duration=duration,
    ))


def all_tools(s: Session) -> List[dict]:
    rows = s.scalars(select(Tool).where(Tool.deleted == False)).all()  # noqa: E712
    return [t.to_dict() for t in rows]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_admin(x_admin_token: Optional[str] = Header(default=None)):
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Token de admin inválido")


# ──────────────────────────── SCHEMAS ────────────────────────────
class CheckoutReq(BaseModel):
    ids: List[str]
    tecnico: str
    area: str
    notes: Optional[str] = ""


class ReserveReq(BaseModel):
    ids: List[str]
    tecnico: str
    area: str


class ActionReq(BaseModel):
    id: str
    tecnico: str
    area: str


class ToolIn(BaseModel):
    id: str
    name: str
    area: str
    image: Optional[str] = None


class ToolUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[str] = None
    image: Optional[str] = None


class ImportReq(BaseModel):
    tools: List[dict] = []
    logs: List[dict] = []
    overwrite: bool = False


# ──────────────────────────── APP ────────────────────────────
app = FastAPI(title="Control de Herramientas — Depósito")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # las apps viven en Netlify/Vercel; CORS abierto
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health():
    return {"ok": True, "service": "deposito", "serverTime": now_ms()}


# ---- LECTURA (la usan las tablets en cada poll) ----
@app.get("/api/state")
def state(db: Session = Depends(get_db)):
    return {"tools": all_tools(db), "serverTime": now_ms()}


@app.get("/api/tools")
def list_tools(db: Session = Depends(get_db)):
    return all_tools(db)


# ---- ACCIONES DE TÉCNICO ----
@app.post("/api/checkout")
def checkout(req: CheckoutReq, db: Session = Depends(get_db)):
    updated, skipped = [], []
    for tid in req.ids:
        tool = db.get(Tool, tid)
        if not tool or tool.deleted:
            skipped.append(tid)
            continue
        if tool.status == "en_uso":
            skipped.append(tid)
            continue
        add_log(db, tool, req.tecnico, req.area, "salida", req.notes)
        tool.status = "en_uso"
        tool.used_by = req.tecnico
        tool.checkout_time = now_ms()
        tool.notes = (req.notes or None)
        tool.reserved_by = None
        tool.reserved_time = None
        updated.append(tid)
    db.commit()
    return {"ok": True, "updated": updated, "skipped": skipped, "tools": all_tools(db)}


@app.post("/api/reserve")
def reserve(req: ReserveReq, db: Session = Depends(get_db)):
    # Regla: 1 reserva activa por técnico
    existing = db.scalars(
        select(Tool).where(
            Tool.status == "reservado",
            Tool.reserved_by == req.tecnico,
            Tool.deleted == False,  # noqa: E712
        )
    ).first()
    if existing:
        raise HTTPException(
            409, f'Ya tienes reservada "{existing.name}". Cancélala primero.'
        )
    reserved = None
    ignored = []
    for i, tid in enumerate(req.ids):
        tool = db.get(Tool, tid)
        if not tool or tool.deleted:
            continue
        if reserved is None and tool.status == "disponible":
            add_log(db, tool, req.tecnico, req.area, "reserva", "Reserva realizada")
            tool.status = "reservado"
            tool.reserved_by = req.tecnico
            tool.reserved_time = now_ms()
            reserved = tid
        else:
            ignored.append(tid)
    db.commit()
    return {"ok": True, "reserved": reserved, "ignored": ignored, "tools": all_tools(db)}


@app.post("/api/use-reserve")
def use_reserve(req: ActionReq, db: Session = Depends(get_db)):
    tool = db.get(Tool, req.id)
    if not tool or tool.deleted:
        raise HTTPException(404, "Herramienta inexistente")
    if tool.status == "en_uso":
        raise HTTPException(409, "La herramienta ya está en uso")
    add_log(db, tool, req.tecnico, req.area, "salida", "Desde reserva propia")
    tool.status = "en_uso"
    tool.used_by = req.tecnico
    tool.checkout_time = now_ms()
    tool.notes = "Desde reserva propia"
    tool.reserved_by = None
    tool.reserved_time = None
    db.commit()
    return {"ok": True, "tools": all_tools(db)}


@app.post("/api/return")
def return_tool(req: ActionReq, db: Session = Depends(get_db)):
    tool = db.get(Tool, req.id)
    if not tool or tool.deleted:
        raise HTTPException(404, "Herramienta inexistente")
    dur = 0
    if tool.checkout_time:
        dur = max(int((now_ms() - tool.checkout_time) / 60000), 1)
    # Cerrar la última 'salida' abierta de esta herramienta
    open_log = db.scalars(
        select(Log).where(
            Log.tool_id == req.id, Log.action == "salida", Log.duration.is_(None)
        ).order_by(Log.id.desc())
    ).first()
    if open_log:
        open_log.duration = dur
    add_log(db, tool, req.tecnico, req.area, "entrada", tool.notes, dur)
    tool.status = "disponible"
    tool.used_by = None
    tool.checkout_time = None
    tool.notes = None
    db.commit()
    return {"ok": True, "tools": all_tools(db)}


@app.post("/api/cancel-reserve")
def cancel_reserve(req: ActionReq, db: Session = Depends(get_db)):
    tool = db.get(Tool, req.id)
    if not tool or tool.deleted:
        raise HTTPException(404, "Herramienta inexistente")
    add_log(db, tool, req.tecnico, req.area, "entrada", "Reserva cancelada")
    tool.status = "disponible"
    tool.reserved_by = None
    tool.reserved_time = None
    db.commit()
    return {"ok": True, "tools": all_tools(db)}


# ---- ADMIN (App Principal) ----
@app.get("/api/logs", dependencies=[Depends(require_admin)])
def get_logs(limit: int = 500, db: Session = Depends(get_db)):
    rows = db.scalars(select(Log).order_by(Log.id.desc()).limit(limit)).all()
    return [r.to_dict() for r in rows]


@app.delete("/api/logs", dependencies=[Depends(require_admin)])
def clear_logs(area: Optional[str] = None, db: Session = Depends(get_db)):
    """Borra el historial. Si se pasa ?area=, borra solo los movimientos de
    técnicos de esa área (techArea)."""
    stmt = delete(Log)
    if area:
        stmt = stmt.where(Log.tech_area == area)
    result = db.execute(stmt)
    db.commit()
    return {"ok": True, "deleted": result.rowcount}


@app.post("/api/tools", dependencies=[Depends(require_admin)])
def create_tool(t: ToolIn, db: Session = Depends(get_db)):
    existing = db.get(Tool, t.id)
    if existing and not existing.deleted:
        raise HTTPException(409, "Ya existe una herramienta con ese ID")
    if existing and existing.deleted:
        existing.deleted = False
        existing.name = t.name
        existing.area = t.area
        existing.image = t.image
        existing.status = "disponible"
    else:
        db.add(Tool(id=t.id, name=t.name, area=t.area, image=t.image,
                    status="disponible"))
    db.commit()
    return {"ok": True, "tools": all_tools(db)}


@app.put("/api/tools/{tool_id}", dependencies=[Depends(require_admin)])
def update_tool(tool_id: str, t: ToolUpdate, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise HTTPException(404, "Herramienta inexistente")
    if t.name is not None:
        tool.name = t.name
    if t.area is not None:
        tool.area = t.area
    if t.image is not None:
        tool.image = t.image
    db.commit()
    return {"ok": True, "tools": all_tools(db)}


@app.delete("/api/tools/{tool_id}", dependencies=[Depends(require_admin)])
def delete_tool(tool_id: str, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise HTTPException(404, "Herramienta inexistente")
    tool.deleted = True  # soft-delete
    db.commit()
    return {"ok": True, "tools": all_tools(db)}


@app.post("/api/import", dependencies=[Depends(require_admin)])
def import_data(req: ImportReq, db: Session = Depends(get_db)):
    """Migración única: empuja el contenido de un localStorage existente al backend."""
    n_tools = 0
    for t in req.tools:
        tid = t.get("id")
        if not tid:
            continue
        tool = db.get(Tool, tid)
        if tool and not req.overwrite:
            continue
        if not tool:
            tool = Tool(id=tid)
            db.add(tool)
        tool.name = t.get("name", tool.name if tool.name else tid)
        tool.area = t.get("area", "cableado")
        tool.image = t.get("image")
        tool.status = t.get("status", "disponible")
        tool.used_by = t.get("usedBy")
        tool.checkout_time = t.get("checkoutTime")
        tool.reserved_by = t.get("reservedBy")
        tool.reserved_time = t.get("reservedTime")
        tool.notes = t.get("notes")
        tool.deleted = bool(t.get("deleted", False))
        n_tools += 1
    n_logs = 0
    for lg in req.logs:
        db.add(Log(
            timestamp=lg.get("timestamp", now_ms()),
            tool_id=lg.get("toolId", ""),
            tool_name=lg.get("toolName", ""),
            tool_area=lg.get("toolArea", ""),
            tech_area=lg.get("techArea", ""),
            tecnico=lg.get("tecnico", ""),
            action=lg.get("action", ""),
            notes=lg.get("notes"),
            duration=lg.get("duration"),
        ))
        n_logs += 1
    db.commit()
    return {"ok": True, "imported_tools": n_tools, "imported_logs": n_logs}
