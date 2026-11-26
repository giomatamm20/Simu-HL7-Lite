"""SQLite state belonging only to Lite; one analyzer profile by construction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import hashlib
from .hl7 import CATALOG
from .design import default_design

DEFAULT_PROFILE = {"name": "Analizador Lite", "application": "SIM_LITE", "facility": "DEMO",
                   "destinationHost": "127.0.0.1", "destinationPort": 2575}


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.depth = 0
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS catalog (code TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS design (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL,
                    configured INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, created TEXT NOT NULL,
                    direction TEXT NOT NULL, raw TEXT NOT NULL, ack TEXT NOT NULL,
                    status TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (sender TEXT NOT NULL, control TEXT NOT NULL,
                    raw TEXT NOT NULL, ack TEXT NOT NULL, PRIMARY KEY(sender, control));
            """)
            db.execute("INSERT OR IGNORE INTO profile VALUES (1, ?)", (json.dumps(DEFAULT_PROFILE),))
            db.execute("INSERT OR IGNORE INTO design(id,data) VALUES (1,?)", (json.dumps(default_design()),))
            for test in CATALOG:
                db.execute("INSERT OR IGNORE INTO catalog VALUES (?, ?)", (test['code'], json.dumps(test)))
            # Interrupted network sends must be reviewable and retryable after restart.
            for row in db.execute("SELECT id, data FROM orders").fetchall():
                order = json.loads(row["data"])
                if order["status"] == "SENDING":
                    order["status"] = "READY"
                    order["lastError"] = "Envío interrumpido. Verifica el destino antes de reenviar."
                    db.execute("UPDATE orders SET data=? WHERE id=?", (json.dumps(order), row["id"]))
            db.execute("UPDATE messages SET status='ERROR', detail='Envío interrumpido; confirma en el destino.' WHERE status='SENDING'")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.depth += 1
            try:
                yield self.connection
                if self.depth == 1:
                    self.connection.commit()
            except Exception:
                if self.depth == 1:
                    self.connection.rollback()
                raise
            finally:
                self.depth -= 1

    def catalog(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute("SELECT data FROM catalog ORDER BY rowid")]

    def catalog_hash(self):
        return hashlib.sha256(json.dumps(self.catalog(), sort_keys=True).encode()).hexdigest()

    def replace_catalog(self, tests, expected_hash):
        with self.transaction() as db:
            if self.catalog_hash() != expected_hash:
                raise ValueError("El catálogo cambió en otra sesión. Recarga la página antes de guardar.")
            orders = self.orders()
            if any(order['status'] == 'SENDING' for order in orders):
                raise ValueError("Espera a que termine el envío antes de modificar los analitos.")
            previous = {test['code']: test for test in self.catalog()}
            updated = {test['code']: test for test in tests}
            if len(updated) != len(tests):
                raise ValueError("Cada analito necesita un código único.")
            if not any(test.get('enabled', True) for test in tests):
                raise ValueError("Mantén al menos un analito activo para poder probar el mensaje.")
            removed = set(previous) - set(updated)
            if any(removed.intersection(order['tests']) for order in orders):
                raise ValueError("No puedes quitar o renombrar un código usado en órdenes. Puedes desactivar el analito.")
            for code in previous.keys() & updated.keys():
                if previous[code] != updated[code]:
                    self.invalidate("Analito modificado: vuelve a preparar el resultado.", code=code)
            db.execute("DELETE FROM catalog")
            db.executemany("INSERT INTO catalog VALUES (?,?)", [(test['code'], json.dumps(test)) for test in tests])
        return {"catalog": self.catalog(), "catalogHash": self.catalog_hash()}

    def save_test(self, test):
        with self.transaction() as db:
            old = db.execute("SELECT data FROM catalog WHERE code=?", (test['code'],)).fetchone()
            db.execute("INSERT INTO catalog VALUES (?, ?) ON CONFLICT(code) DO UPDATE SET data=excluded.data",
                       (test['code'], json.dumps(test)))
            if old and json.loads(old[0]) != test:
                self.invalidate("Analito modificado: vuelve a preparar el resultado.", code=test['code'])
        return test

    def design(self):
        with self.lock:
            row = self.connection.execute("SELECT data,configured,revision FROM design WHERE id=1").fetchone()
            return {"configuration": json.loads(row[0]), "configured": bool(row[1]), "revision": row[2]}

    def save_design(self, configuration):
        with self.transaction() as db:
            if any(order['status'] == 'SENDING' for order in self.orders()):
                raise ValueError("Espera a que termine el envío antes de modificar el diseño.")
            db.execute("UPDATE design SET data=?,configured=1,revision=revision+1 WHERE id=1", (json.dumps(configuration),))
            self.invalidate("Diseño actualizado: vuelve a preparar el resultado.")
        return self.design()

    def invalidate(self, reason, code=None):
        for order in self.orders():
            if order['status'] == 'READY' and (code is None or code in order['tests']):
                order.update(status="DRAFT", lastError=reason)
                self.save_order(order)

    def profile(self):
        with self.lock:
            return json.loads(self.connection.execute("SELECT data FROM profile WHERE id=1").fetchone()[0])

    def save_profile(self, profile):
        with self.transaction() as db:
            previous = self.profile()
            if any(order['status'] == 'SENDING' for order in self.orders()):
                raise ValueError("Espera a que termine el envío antes de modificar el equipo.")
            db.execute("UPDATE profile SET data=? WHERE id=1", (json.dumps(profile),))
            if any(previous[key] != profile[key] for key in ('application', 'facility')):
                self.invalidate("Identidad HL7 modificada: vuelve a preparar el resultado.")
        return profile

    def orders(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.connection.execute("SELECT data FROM orders ORDER BY rowid DESC")]

    def order(self, identifier):
        with self.lock:
            row = self.connection.execute("SELECT data FROM orders WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError("Orden no encontrada.")
            return json.loads(row[0])

    def create_order(self, order):
        order = {**order, "status": "PENDING", "created": now(), "rawResult": "", "values": {}, "lastError": ""}
        with self.transaction() as db:
            try:
                db.execute("INSERT INTO orders VALUES (?, ?)", (order["orderId"], json.dumps(order)))
            except sqlite3.IntegrityError as error:
                raise ValueError("Ya existe esa orden; no se sobrescribieron sus datos.") from error
        return order

    def save_order(self, order):
        with self.transaction() as db:
            db.execute("UPDATE orders SET data=? WHERE id=?", (json.dumps(order), order["orderId"]))
        return order

    def log(self, direction, raw, ack="", status="RECEIVED", detail=""):
        with self.transaction() as db:
            cursor = db.execute("INSERT INTO messages(created,direction,raw,ack,status,detail) VALUES(?,?,?,?,?,?)",
                                (now(), direction, raw, ack, status, detail))
            # A bounded visible history; orders and duplicate receipts remain persisted.
            db.execute("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 200) AND status != 'SENDING'")
            return cursor.lastrowid

    def finish_log(self, identifier, ack, status, detail):
        with self.transaction() as db:
            db.execute("UPDATE messages SET ack=?,status=?,detail=? WHERE id=?", (ack, status, detail, identifier))

    def messages(self):
        with self.lock:
            return [dict(row) for row in self.connection.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 200")]

    def close(self):
        self.connection.close()
