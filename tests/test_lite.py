"""End-to-end demo checks using synthetic data and real loopback MLLP sockets."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from fastapi.testclient import TestClient

from lite_app import hl7, mllp
from lite_app.main import create_app
from lite_app.store import DEFAULT_PROFILE

ORDER = (Path(__file__).resolve().parents[1] / "samples/order-glucose.hl7").read_text(encoding="utf-8")


@contextmanager
def peer(reply):
    server = mllp.Server(("127.0.0.1", 0), reply)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class LiteCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hl7-lite-tests-")
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "demo.db")
        self.client = self.enterContext(TestClient(create_app(self.path, "127.0.0.1", 0)))
        self.demo = self.client.app.state.demo
        self.port = self.demo.listener.server_address[1]

    def state(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def create(self, tests=None):
        response = self.client.post("/api/orders", json={"orderId": "MANUAL-1", "patientId": "DEMO-P1",
                                    "patientName": "Paciente ficticio", "tests": tests or ["GLU"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def prepare(self, identifier="MANUAL-1", values=None):
        response = self.client.post(f"/api/orders/{identifier}/results", json={"values": values or {"GLU": "91.5"}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def destination(self, port):
        response = self.client.put("/api/profile", json={**DEFAULT_PROFILE, "destinationPort": port})
        self.assertEqual(response.status_code, 200, response.text)

    def test_receive_order_and_duplicate_over_real_mllp(self):
        first = mllp.send("127.0.0.1", self.port, ORDER)
        second = mllp.send("127.0.0.1", self.port, ORDER)
        self.assertIn("MSA|AA|DEMO-MSG-001", first)
        self.assertEqual(first, second)
        state = self.state()
        self.assertEqual(len(state["orders"]), 1)
        self.assertEqual(state["orders"][0]["status"], "PENDING")
        self.assertEqual(state["orders"][0]["rawResult"], "")
        self.assertEqual(state["messages"][0]["status"], "DUPLICATE")

    def test_control_id_conflict_does_not_replace_order(self):
        mllp.send("127.0.0.1", self.port, ORDER)
        response = mllp.send("127.0.0.1", self.port, ORDER.replace("PAC-DEMO-001", "CHANGED"))
        self.assertIn("MSA|AR|", response)
        self.assertEqual(self.state()["orders"][0]["patientId"], "PAC-DEMO-001")

    def test_unknown_test_and_multiple_obr_are_rejected(self):
        for raw in (ORDER.replace("GLU^Glucosa", "UNCONFIGURED^Test"), ORDER + "OBR|2|OTHER||GLU^Glucosa\r"):
            with self.subTest(raw=raw):
                self.assertIn("MSA|AR|", mllp.send("127.0.0.1", self.port, raw))
        self.assertEqual(self.state()["orders"], [])

    def test_manual_workflow_receives_positive_ack(self):
        self.create()
        prepared = self.prepare()
        self.assertIn("OBX|1|NM|GLU^Glucosa||91.5|mg/dL|||||F", prepared["rawResult"])
        self.assertFalse(hl7.validate(prepared["rawResult"]))
        self.assertEqual(hl7.rows(prepared["rawResult"])[0][10], "T")
        with peer(lambda raw: hl7.ack(raw, DEFAULT_PROFILE)) as port:
            self.destination(port)
            result = self.client.post("/api/orders/MANUAL-1/send", json={}).json()
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(self.state()["orders"][0]["status"], "SENT")
        self.assertEqual(self.client.post("/api/orders/MANUAL-1/send", json={}).status_code, 400)

    def test_rejection_and_wrong_ack_id_keep_result_for_retry(self):
        self.create()
        original = self.prepare()["rawResult"]
        replies = [lambda raw: hl7.ack(raw, DEFAULT_PROFILE, "AE", "Test rejection"),
                   lambda raw: hl7.ack(raw, DEFAULT_PROFILE).replace(hl7.rows(raw)[0][9], "WRONG-ID")]
        for reply in replies:
            with peer(reply) as port:
                self.destination(port)
                result = self.client.post("/api/orders/MANUAL-1/send", json={}).json()
            self.assertIn(result["status"], {"REJECTED", "ERROR"})
            order = self.state()["orders"][0]
            self.assertEqual(order["status"], "READY")
            self.assertEqual(order["rawResult"], original)
        with peer(lambda raw: hl7.ack(raw, DEFAULT_PROFILE)) as port:
            self.destination(port)
            self.assertEqual(self.client.post("/api/orders/MANUAL-1/send", json={}).json()["status"], "ACCEPTED")

    def test_concurrent_send_cannot_send_or_edit_the_same_order_twice(self):
        self.create()
        self.prepare()
        entered, release = threading.Event(), threading.Event()
        received = []
        def delayed_ack(raw):
            received.append(raw)
            entered.set()
            release.wait(timeout=4)
            return hl7.ack(raw, DEFAULT_PROFILE)
        with peer(delayed_ack) as port, ThreadPoolExecutor(max_workers=1) as pool:
            self.destination(port)
            pending = pool.submit(self.client.post, "/api/orders/MANUAL-1/send", json={})
            try:
                self.assertTrue(entered.wait(timeout=2))
                self.assertEqual(self.client.post("/api/orders/MANUAL-1/send", json={}).status_code, 400)
                self.assertEqual(self.client.post("/api/orders/MANUAL-1/results", json={"values": {"GLU": "99"}}).status_code, 400)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=3).json()["status"], "ACCEPTED")
        self.assertEqual(len(received), 1)

    def test_connection_failure_is_logged_and_retryable(self):
        self.create()
        self.prepare()
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            self.destination(unused.getsockname()[1])
            response = self.client.post("/api/orders/MANUAL-1/send", json={})
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertEqual(self.state()["orders"][0]["status"], "READY")
        self.assertEqual(self.state()["messages"][0]["status"], "ERROR")

    def test_catalog_and_order_have_no_five_test_limit(self):
        codes = ["GLU", "UREA", "CREA", "HGB", "WBC"]
        for index in range(20):
            code = f"TEST{index}"
            response = self.client.put("/api/catalog", json={"code": code, "name": f"Prueba {index}", "unit": "U/L"})
            self.assertEqual(response.status_code, 200)
            codes.append(code)
        self.assertEqual(len(self.state()["catalog"]), 25)
        self.assertIsNone(self.state()["limits"]["tests"])
        self.create(codes)
        prepared = self.prepare(values={code: "1.2" for code in codes})
        self.assertEqual(len([row for row in hl7.rows(prepared["rawResult"]) if row[0] == "OBX"]), 25)

    def test_input_escaping_and_finite_numbers(self):
        response = self.client.post("/api/orders", json={"orderId": "ESCAPE", "patientId": "P|1", "patientName": "<script>^demo", "tests": ["GLU"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["patientId"], r"P\F\1")
        for value in ("NaN", "Infinity", "1e900", "1|2", ""):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/orders/ESCAPE/results", json={"values": {"GLU": value}}).status_code, 400)
        self.assertEqual(self.client.post("/api/orders/ESCAPE/results", json={"values": {"GLU": "1", "UREA": "2"}}).status_code, 400)

    def test_no_premium_routes_or_second_profile(self):
        self.assertEqual(self.client.post("/api/profile", json={}).status_code, 405)
        for route in ("analyzers", "automation", "pacs", "profiles/import", "semantic", "templates"):
            self.assertEqual(self.client.post("/api/" + route, json={}).status_code, 404)
        self.assertEqual(self.state()["premium"], {"available": False, "messageOnly": True})
        self.assertIn("Esta función requiere la versión Premium.", self.client.get("/").text)

    def test_cross_origin_write_is_blocked(self):
        self.assertEqual(self.client.put("/api/profile", json=DEFAULT_PROFILE, headers={"Origin": "https://example.invalid"}).status_code, 403)
        self.assertEqual(self.client.put("/api/profile", content="{}", headers={"Content-Type": "text/plain"}).status_code, 415)

    def test_fragmented_and_back_to_back_frames(self):
        packet = mllp.START + hl7.normalize(ORDER).encode() + mllp.END
        parser = mllp.Frames()
        self.assertEqual(parser.feed(packet[:13]), [])
        self.assertEqual(parser.feed(packet[13:] + packet), [hl7.normalize(ORDER)] * 2)
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
            connection.sendall(packet[:7])
            connection.sendall(packet[7:] + packet)
            connection.settimeout(2)
            replies = []
            parser = mllp.Frames()
            while len(replies) < 2:
                replies.extend(parser.feed(connection.recv(8192)))
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(len(self.state()["orders"]), 1)

    def test_validation_and_frame_bounds(self):
        self.assertFalse(self.client.post("/api/validate", json={"raw": "PID|1"}).json()["valid"])
        self.assertIn("MSA|AR|", mllp.send("127.0.0.1", self.port, ""))
        with self.assertRaises(ValueError):
            mllp.Frames().feed(mllp.START + b"x" * (mllp.MAX_FRAME + 4))

    def test_state_survives_restart_including_catalog_and_receipts(self):
        self.client.put("/api/catalog", json={"code": "ALT", "name": "ALT demo", "unit": "U/L"})
        original_ack = mllp.send("127.0.0.1", self.port, ORDER)
        self.prepare("DEMO-ORD-001")
        with TestClient(create_app(self.path, "127.0.0.1", 0)) as restarted:
            state = restarted.get("/api/state").json()
            self.assertIn("ALT", [test["code"] for test in state["catalog"]])
            self.assertEqual(state["orders"][0]["status"], "READY")
            self.assertEqual(mllp.send("127.0.0.1", state["listener"]["port"], ORDER), original_ack)


if __name__ == "__main__":
    unittest.main()
