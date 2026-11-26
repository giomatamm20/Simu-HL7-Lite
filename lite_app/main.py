"""Public demo entry point. Run one worker: there is one MLLP listener."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
import threading
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import hl7, mllp
from .store import Store

ROOT = Path(__file__).resolve().parent


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Profile(Model):
    name: str = Field(min_length=1, max_length=80)
    application: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    facility: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    destinationHost: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9_.:\-]+$")
    destinationPort: int = Field(ge=1, le=65535, strict=True)


class ManualOrder(Model):
    orderId: str = Field(default_factory=lambda: "DEMO-" + uuid4().hex[:10], min_length=1, max_length=80)
    patientId: str = Field(min_length=1, max_length=80)
    patientName: str = Field(min_length=1, max_length=100)
    tests: list[str] = Field(min_length=1)

    @field_validator("tests")
    @classmethod
    def catalog_only(cls, tests):
        if len(set(tests)) != len(tests):
            raise ValueError("Selecciona pruebas sin repetir.")
        return tests

    @field_validator("orderId", "patientId", "patientName")
    @classmethod
    def printable(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("No se admiten caracteres de control.")
        return value


class Values(Model):
    values: dict[str, str] = Field(min_length=1)


class CatalogTest(Model):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(default="", max_length=60)

    @field_validator("name", "unit")
    @classmethod
    def printable(cls, value):
        if any(ord(char) < 32 for char in value):
            raise ValueError("No se admiten caracteres de control.")
        return value


class Message(Model):
    raw: str = Field(min_length=1, max_length=mllp.MAX_FRAME)


class Demo:
    def __init__(self, db_path, listen_host, listen_port):
        self.store = Store(db_path)
        try:
            self.listener = mllp.Server((listen_host, listen_port), self.receive)
        except Exception:
            self.store.close()
            raise
        self.thread = threading.Thread(target=self.listener.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.listener.shutdown()
        self.listener.server_close()
        self.thread.join(timeout=3)
        # server_close waits for bounded handlers before SQLite is closed.
        with self.store.lock:
            self.store.close()

    def receive(self, raw):
        raw = hl7.normalize(raw)
        errors = hl7.validate(raw)
        msh = next(iter(hl7.rows(raw)), [])
        kind = hl7.field(msh, 8).split("^")[0] if hl7.field(msh, 0) == "MSH" else ""
        with self.store.transaction():
            profile = self.store.profile()
            if kind == "ACK":
                self.store.log("RX", raw, status="INVALID" if errors else "RECEIVED", detail="; ".join(errors))
                return ""  # Never ACK an ACK.
            if errors:
                reply = hl7.ack(raw, profile, "AR", "; ".join(errors))
                self.store.log("RX", raw, reply, "REJECTED", "; ".join(errors))
                return reply
            sender = "|".join(msh[2:4])
            control = hl7.field(msh, 9)
            db = self.store.connection
            receipt = db.execute("SELECT raw,ack FROM receipts WHERE sender=? AND control=?", (sender, control)).fetchone()
            if receipt:
                if receipt["raw"] == raw:
                    reply = receipt["ack"]
                    self.store.log("RX", raw, reply, "DUPLICATE", "Mensaje repetido; se conserva el ACK original.")
                else:
                    reply = hl7.ack(raw, profile, "AR", "MSH-10 reutilizado con contenido diferente.")
                    self.store.log("RX", raw, reply, "REJECTED", "Conflicto de MSH-10.")
                return reply
            try:
                if kind == "ORM":
                    self.store.create_order(hl7.order_from_message(raw, self.store.catalog()))
                reply = hl7.ack(raw, profile)
                status, detail = "RECEIVED", "Orden pendiente de valores manuales." if kind == "ORM" else "Mensaje recibido."
            except ValueError as error:
                detail, status = str(error), "REJECTED"
                reply = hl7.ack(raw, profile, "AR", detail)
            with self.store.transaction() as transaction:
                transaction.execute("INSERT INTO receipts VALUES (?,?,?,?)", (sender, control, raw, reply))
            self.store.log("RX", raw, reply, status, detail)
            return reply

    def prepare(self, identifier, values):
        with self.store.lock:
            order = self.store.order(identifier)
            if order["status"] in {"SENDING", "SENT"}:
                raise ValueError("La orden está en envío o ya fue aceptada; conserva su resultado.")
            order["rawResult"] = hl7.result_message(self.store.profile(), order, values, self.store.catalog())
            order.update(values=values, status="READY", lastError="")
            return self.store.save_order(order)

    def transmit(self, raw, identifier=None):
        errors = hl7.validate(raw)
        if errors:
            raise ValueError("; ".join(errors))
        profile = self.store.profile()
        if profile["destinationPort"] == self.listener.server_address[1] and profile["destinationHost"] in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("El destino debe ser otro listener, no el propio Lite.")
        log_id = self.store.log("TX", raw, status="SENDING")
        reply, status, detail = "", "ERROR", ""
        try:
            reply = mllp.send(profile["destinationHost"], profile["destinationPort"], raw)
            reply_errors = hl7.validate(reply)
            msa = next((row for row in hl7.rows(reply) if row[0] == "MSA"), [])
            if reply_errors or not hl7.field(hl7.rows(reply)[0], 8).startswith("ACK") or hl7.field(msa, 2) != hl7.field(hl7.rows(raw)[0], 9):
                raise ValueError("ACK inválido o MSA-2 no coincide con MSH-10.")
            status = "ACCEPTED" if hl7.field(msa, 1) == "AA" else "REJECTED"
            detail = "ACK " + hl7.field(msa, 1)
        except (OSError, ValueError, UnicodeError) as error:
            detail = str(error)
        finally:
            self.store.finish_log(log_id, reply, status, detail)
            if identifier is not None:
                with self.store.lock:
                    order = self.store.order(identifier)
                    order.update(status="SENT" if status == "ACCEPTED" else "READY", lastError="" if status == "ACCEPTED" else detail)
                    self.store.save_order(order)
        return {"ack": reply, "status": status, "detail": detail}

    def send_order(self, identifier):
        with self.store.lock:
            order = self.store.order(identifier)
            if order["status"] != "READY":
                raise ValueError("La orden debe tener un resultado preparado y no estar en envío.")
            order["status"] = "SENDING"
            self.store.save_order(order)
        try:
            return self.transmit(order["rawResult"], identifier)
        except Exception:
            with self.store.lock:
                order = self.store.order(identifier)
                order["status"] = "READY"
                self.store.save_order(order)
            raise


def create_app(db_path=None, listen_host=None, listen_port=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.demo = Demo(db_path or os.environ.get("LITE_DB_PATH", "data/lite.db"),
                              listen_host or os.environ.get("LITE_MLLP_HOST", "127.0.0.1"),
                              listen_port if listen_port is not None else int(os.environ.get("LITE_MLLP_PORT", "5200")))
        try:
            yield
        finally:
            app.state.demo.close()

    app = FastAPI(title="HL7 Simulator Lite", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_ui(request: Request, call_next):
        # Cross-origin writes from other websites are not part of this local demo.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Origen no autorizado."}, status_code=403)
            if "application/json" not in request.headers.get("content-type", ""):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Se requiere application/json."}, status_code=415)
        return await call_next(request)

    def demo():
        return app.state.demo

    def action(function, *args):
        try:
            return function(*args)
        except KeyError as error:
            raise HTTPException(404, "Orden no encontrada.") from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/api/state")
    def state():
        return {"edition": "Lite", "profile": demo().store.profile(), "catalog": demo().store.catalog(),
                "orders": demo().store.orders(), "messages": demo().store.messages(),
                "listener": {"host": demo().listener.server_address[0], "port": demo().listener.server_address[1]},
                "limits": {"profiles": 1, "tests": None, "manualResults": True},
                "premium": {"available": False, "messageOnly": True}}

    @app.put("/api/profile")
    def profile(payload: Profile):
        return demo().store.save_profile(payload.model_dump())

    @app.post("/api/orders")
    def create_order(payload: ManualOrder):
        order = payload.model_dump()
        if not set(order['tests']).issubset({test['code'] for test in demo().store.catalog()}):
            raise HTTPException(400, "Agrega primero las pruebas al catálogo.")
        for key in ("orderId", "patientId", "patientName"):
            order[key] = hl7.scalar(order[key])
        order.update(sampleId=order["orderId"], source="MANUAL")
        return action(demo().store.create_order, order)

    @app.put("/api/catalog")
    def save_test(payload: CatalogTest):
        return demo().store.save_test(payload.model_dump())

    @app.post("/api/orders/{identifier:path}/results")
    def prepare(identifier: str, payload: Values):
        return action(demo().prepare, identifier, payload.values)

    @app.post("/api/orders/{identifier:path}/send")
    def send_order(identifier: str):
        return action(demo().send_order, identifier)

    @app.post("/api/validate")
    def validate(payload: Message):
        errors = hl7.validate(payload.raw)
        return {"valid": not errors, "errors": errors}

    @app.post("/api/send")
    def send(payload: Message):
        return action(demo().transmit, hl7.normalize(payload.raw))

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


app = create_app()
