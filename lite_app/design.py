"""One manually configured result message; no semantic inference or design library."""
import copy
import re
from uuid import uuid4

from . import hl7
from .mllp import MAX_FRAME

SOURCES = {
    "fixed": "Valor fijo", "profile.application": "Aplicación del equipo", "profile.facility": "Instalación del equipo",
    "system.timestamp": "Fecha y hora", "system.controlId": "ID único del mensaje", "counter": "Número de repetición",
    "patient.id": "ID del paciente", "patient.name": "Nombre del paciente", "order.id": "ID de la orden",
    "sample.id": "ID de muestra", "study.code": "Código del estudio", "study.name": "Nombre del estudio",
    "result.code": "Código del analito", "result.name": "Nombre del analito", "result.type": "Tipo del valor",
    "result.value": "Valor manual", "result.unit": "Unidad", "result.reference": "Intervalo de referencia",
    "result.flag": "Bandera manual", "result.status": "Estado del resultado",
}


def mapping(field, source="fixed", value="", component=None):
    return {"field": field, "component": component, "source": source, "value": value}


DEFAULT_DESIGN = {"name": "Resultado de laboratorio", "segments": [
    {"name": "MSH", "repeat": False, "fields": [mapping(2, value="^~\\&"), mapping(3, "profile.application"),
        mapping(4, "profile.facility"), mapping(5, value="LIS"), mapping(6, value="DEMO"), mapping(7, "system.timestamp"),
        mapping(9, value="ORU^R01"), mapping(10, "system.controlId"), mapping(11, value="T"),
        mapping(12, value="2.5.1"), mapping(18, value="UNICODE UTF-8")]},
    {"name": "PID", "repeat": False, "fields": [mapping(1, value="1"), mapping(3, "patient.id"), mapping(5, "patient.name")]},
    {"name": "ORC", "repeat": False, "fields": [mapping(1, value="RE"), mapping(2, "order.id"), mapping(3, "sample.id")]},
    {"name": "OBR", "repeat": False, "fields": [mapping(1, value="1"), mapping(2, "order.id"), mapping(3, "sample.id"),
        mapping(4, "study.code", component=1), mapping(4, "study.name", component=2)]},
    {"name": "OBX", "repeat": True, "fields": [mapping(1, "counter"), mapping(2, "result.type"),
        mapping(3, "result.code", component=1), mapping(3, "result.name", component=2), mapping(5, "result.value"),
        mapping(6, "result.unit"), mapping(7, "result.reference"), mapping(8, "result.flag"), mapping(11, "result.status")]},
]}


def default_design():
    return copy.deepcopy(DEFAULT_DESIGN)


def render(configuration, profile, order, values, catalog, control_id=None, timestamp=None):
    if set(values) != set(order["tests"]):
        raise ValueError("Ingresa exactamente un valor por cada analito de la orden.")
    by_code = {test["code"]: test for test in catalog}
    results = []
    for code in order["tests"]:
        test = by_code[code]
        value_type = test.get("valueType", "NM")
        value = hl7.numeric(values[code]) if value_type == "NM" else str(values[code])
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("El resultado de texto debe tener contenido y no contener caracteres de control.")
        results.append({"result.code": code, "result.name": test["name"], "result.type": value_type,
                        "result.value": value, "result.unit": test.get("unit", ""),
                        "result.reference": test.get("reference", ""), "result.flag": test.get("flag", ""), "result.status": "F"})
    context = {"profile.application": profile["application"], "profile.facility": profile["facility"],
               "system.timestamp": timestamp or hl7.stamp(), "system.controlId": control_id or uuid4().hex,
               "patient.id": order["patientId"], "patient.name": order["patientName"], "order.id": order["orderId"],
               "sample.id": order["sampleId"], "study.code": order.get("studyCode") or "LAB",
               "study.name": order.get("studyName") or "Estudio de laboratorio"}
    # These fields were already validated/escaped on receipt or manual order entry.
    structural = {"patient.id", "patient.name", "order.id", "sample.id", "study.code", "study.name"}
    output = []
    for segment in configuration["segments"]:
        for index, result in enumerate(results if segment.get("repeat") else [results[0]], 1):
            current = {**context, **result, "counter": str(index)}
            row = [segment["name"]]
            for item in segment["fields"]:
                source = item["source"]
                value = item.get("value", "") if source == "fixed" else str(current[source])
                if source != "fixed" and source not in structural:
                    value = hl7.scalar(value)
                elif source in structural and item.get("component"):
                    # A component mapping must not introduce extra components.
                    value = value.replace("^", "\\S\\").replace("~", "\\R\\").replace("&", "\\T\\")
                position = item["field"] - (1 if segment["name"] == "MSH" else 0)
                while len(row) <= position:
                    row.append("")
                component = item.get("component")
                if component:
                    parts = row[position].split("^")
                    while len(parts) < component:
                        parts.append("")
                    parts[component - 1] = value
                    row[position] = "^".join(parts)
                else:
                    row[position] = value
            output.append("|".join(row))
    raw = "\r".join(output) + "\r"
    if not raw.isascii() and hl7.field(hl7.rows(raw)[0], 17) not in {"UNICODE UTF-8", "UTF-8"}:
        raise ValueError("El mensaje contiene texto no ASCII: configura MSH-18 como UNICODE UTF-8.")
    if len(raw.encode("utf-8")) > MAX_FRAME:
        raise ValueError("El mensaje supera el tamaño máximo de un bloque MLLP (256 KiB).")
    errors = hl7.validate(raw)
    if errors:
        raise ValueError("; ".join(errors))
    return raw


def example(catalog, limit=3):
    tests = catalog[:limit] if limit is not None else catalog
    order = {"orderId": "ORD-DEMO", "sampleId": "TUBO-DEMO", "patientId": "PAC-DEMO",
             "patientName": "PACIENTE^FICTICIO", "studyCode": "LAB", "studyName": "Estudio de laboratorio",
             "tests": [test["code"] for test in tests]}
    values = {test["code"]: "1.23" if test.get("valueType", "NM") == "NM" else "DEMO" for test in tests}
    return order, values


def validate_design(configuration, profile, catalog):
    segments = configuration["segments"]
    if not segments or segments[0]["name"] != "MSH" or sum(s["name"] == "MSH" for s in segments) != 1:
        raise ValueError("El diseño debe comenzar por un único MSH.")
    for required in ("PID", "OBR", "OBX"):
        if not any(s["name"] == required for s in segments):
            raise ValueError("Falta el segmento " + required + ".")
    for segment in segments:
        if not re.fullmatch(r"[A-Z][A-Z0-9]{2}", segment["name"]):
            raise ValueError("Nombre de segmento inválido.")
        if segment["name"] == "MSH" and segment.get("repeat"):
            raise ValueError("MSH no se puede repetir.")
        targets = set()
        for item in segment["fields"]:
            target = (item["field"], item.get("component"))
            if target in targets or ((item["field"], None) in targets) or (target[1] is None and any(t[0] == target[0] for t in targets)):
                raise ValueError("Hay campos o componentes superpuestos en " + segment["name"] + ".")
            targets.add(target)
            if segment["name"] == "MSH" and item["field"] < 2:
                raise ValueError("MSH-1 es el separador | y no se edita como campo.")
            if item["source"] not in SOURCES:
                raise ValueError("Origen desconocido: " + item["source"])
            fixed = item.get("value", "")
            if "|" in fixed or any(ord(char) < 32 for char in fixed):
                raise ValueError("Usa escapes HL7 para separadores y no incluyas saltos de línea en un campo.")
            if item["source"].startswith("result.") or item["source"] == "counter":
                if not segment.get("repeat"):
                    raise ValueError("Los campos de analitos requieren un segmento repetido por analito.")
        if segment["name"] == "OBX" and not segment.get("repeat"):
            raise ValueError("OBX debe repetirse por cada analito de la orden.")
    msh = {item["field"]: item for item in segments[0]["fields"] if not item.get("component")}
    if msh.get(2, {}).get("value") != "^~\\&" or msh.get(2, {}).get("source") != "fixed":
        raise ValueError("MSH-2 debe ser el valor fijo ^~\\&.")
    if msh.get(9, {}).get("value") != "ORU^R01" or msh.get(9, {}).get("source") != "fixed":
        raise ValueError("El único mensaje configurado en Lite es el resultado ORU^R01.")
    if msh.get(10, {}).get("source") != "system.controlId":
        raise ValueError("MSH-10 debe usar el ID único del mensaje.")
    order, values = example(catalog)
    return render(configuration, profile, order, values, catalog, control_id="VISTA-PREVIA", timestamp="20260101120000+0000")
