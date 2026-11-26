"""Small, fixed HL7 v2.5.1 subset for the public demo."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

CATALOG = [
    {"code": "GLU", "name": "Glucosa", "unit": "mg/dL"},
    {"code": "UREA", "name": "Urea", "unit": "mg/dL"},
    {"code": "CREA", "name": "Creatinina", "unit": "mg/dL"},
    {"code": "HGB", "name": "Hemoglobina", "unit": "g/dL"},
    {"code": "WBC", "name": "Leucocitos", "unit": "10*3/uL"},
]


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S+0000")


def field(row, index):
    return row[index] if len(row) > index else ""


def normalize(raw):
    return "\r".join(line for line in raw.replace("\r\n", "\r").replace("\n", "\r").split("\r") if line) + "\r"


def rows(raw):
    return [line.split("|") for line in normalize(raw).split("\r") if line]


def validate(raw):
    data = rows(raw)
    errors = []
    if not data or data[0][0] != "MSH":
        return ["El primer segmento debe ser MSH." ]
    msh = data[0]
    if field(msh, 1) != "^~\\&":
        errors.append("Lite acepta los separadores estándar | y ^~\\&.")
    for index, label in [(8, "MSH-9 tipo"), (9, "MSH-10 identificador"), (10, "MSH-11 procesamiento"), (11, "MSH-12 versión")]:
        if not field(msh, index):
            errors.append(f"Falta {label}.")
    if any(ord(char) < 32 and char != "\r" for char in normalize(raw)):
        errors.append("Hay caracteres de control no permitidos.")
    if sum(row[0] == "MSH" for row in data) != 1:
        errors.append("Se admite un solo mensaje MSH por bloque MLLP.")
    if any(not re.fullmatch(r"[A-Z][A-Z0-9]{2}", row[0]) for row in data):
        errors.append("Identificador de segmento inválido.")
    if field(msh, 17) not in {"", "ASCII", "UNICODE UTF-8", "UTF-8"}:
        errors.append("Lite acepta ASCII o UTF-8 en MSH-18.")
    kind = field(msh, 8).split("^")[:2]
    by_type = {name: [row for row in data if row[0] == name] for name in ("PID", "ORC", "OBR", "OBX", "MSA")}
    if kind in [["ORM", "O01"], ["ORU", "R01"]]:
        for name in ("PID", "OBR"):
            if not by_type[name]:
                errors.append(f"Falta segmento {name}.")
        if by_type["PID"] and not field(by_type["PID"][0], 3):
            errors.append("Falta PID-3.")
        if by_type["OBR"] and not field(by_type["OBR"][0], 4):
            errors.append("Falta OBR-4.")
        if kind[0] == "ORM" and not by_type["ORC"]:
            errors.append("Falta segmento ORC.")
        if kind[0] == "ORU":
            if not by_type["OBX"]:
                errors.append("ORU requiere al menos un OBX.")
            for obx in by_type["OBX"]:
                if not all(field(obx, i) for i in (2, 3, 5, 11)):
                    errors.append("OBX requiere tipo, código, valor y estado (2, 3, 5, 11).")
                if field(obx, 2) == "NM":
                    try:
                        numeric(field(obx, 5))
                    except ValueError:
                        errors.append("OBX-5 debe ser numérico finito para OBX-2=NM.")
    elif kind[0] == "ACK":
        if len(by_type["MSA"]) != 1 or field(by_type["MSA"][0], 1) not in {"AA", "AE", "AR"} or not field(by_type["MSA"][0], 2):
            errors.append("ACK requiere MSA con AA/AE/AR y el identificador original.")
    else:
        errors.append("Lite acepta ORM^O01, ORU^R01 y ACK.")
    return errors


def scalar(value):
    text = str(value)
    for char, code in [("\\", "E"), ("|", "F"), ("^", "S"), ("~", "R"), ("&", "T")]:
        text = text.replace(char, f"\\{code}\\")
    return "".join(char for char in text if ord(char) >= 32)


def numeric(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("El resultado debe ser numérico.") from error
    if not number.is_finite() or len(str(value)) > 30 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(value)):
        raise ValueError("Ingresa un número finito de hasta 30 caracteres, sin exponentes.")
    return str(value)


def header(profile, kind, control_id, receiver="LIS", facility="DEMO"):
    data = ["MSH", "^~\\&", scalar(profile["application"]), scalar(profile["facility"]),
            receiver, facility, stamp(), "", kind, control_id, "T", "2.5.1"]
    data.extend([""] * (18 - len(data)))
    data[17] = "UNICODE UTF-8"
    return "|".join(data)


def ack(raw, profile, code="AA", detail="Aceptado"):
    msh = rows(raw)[0] if rows(raw) and rows(raw)[0][0] == "MSH" else []
    return (header(profile, "ACK", uuid4().hex, field(msh, 2), field(msh, 3)) + "\r"
            + f"MSA|{code}|{field(msh, 9)}|{scalar(detail)}\r")


def order_from_message(raw, catalog):
    data = rows(raw)
    group = lambda name: [row for row in data if row[0] == name]
    if any(len(group(name)) != 1 for name in ("PID", "ORC", "OBR")):
        raise ValueError("Lite acepta un PID, un ORC y un OBR por orden.")
    pid, orc, obr = (group(name)[0] for name in ("PID", "ORC", "OBR"))
    if field(orc, 1) != "NW":
        raise ValueError("Lite solo recibe órdenes nuevas: ORC-1=NW.")
    placer = field(orc, 2) or field(obr, 2)
    if not placer or (field(orc, 2) and field(obr, 2) and field(orc, 2) != field(obr, 2)):
        raise ValueError("Se requiere un identificador consistente en ORC-2 / OBR-2.")
    code = field(obr, 4).split("^")[0]
    if code not in {test['code'] for test in catalog}:
        raise ValueError("Agrega primero la prueba al catálogo: " + code)
    study = field(obr, 4).split("^")
    return {"orderId": placer, "sampleId": field(obr, 3) or placer, "studyCode": study[0],
            "studyName": study[1] if len(study) > 1 else study[0],
            "patientId": field(pid, 3), "patientName": field(pid, 5), "tests": [code], "source": "MLLP"}


def result_message(profile, order, values, catalog):
    if set(values) != set(order["tests"]):
        raise ValueError("Debes ingresar exactamente un valor por cada prueba de la orden.")
    control_id = uuid4().hex
    # Received identifiers may already contain composites and HL7 escapes.
    # Manual scalar identifiers are escaped before they enter the order store.
    data = [header(profile, "ORU^R01", control_id),
            f"PID|1||{order['patientId']}||{order['patientName']}",
            f"ORC|RE|{order['orderId']}|{order['sampleId']}",
            f"OBR|1|{order['orderId']}|{order['sampleId']}|LITE^Resultados manuales"]
    by_code = {test['code']: test for test in catalog}
    for index, code in enumerate(order["tests"], 1):
        test = by_code[code]
        data.append(f"OBX|{index}|NM|{code}^{scalar(test['name'])}||{numeric(values[code])}|{scalar(test['unit'])}|||||F")
    return "\r".join(data) + "\r"
