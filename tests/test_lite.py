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
from lite_app.design import default_design
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
        response = self.client.put('/api/design', json=default_design())
        self.assertEqual(response.status_code, 200, response.text)

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

    def test_standby_until_one_valid_design_is_activated(self):
        with TestClient(create_app(str(Path(self.directory.name) / 'fresh.db'), '127.0.0.1', 0)) as client:
            state = client.get('/api/state').json()
            self.assertEqual(state['equipment']['state'], 'STANDBY')
            self.assertEqual(client.post('/api/orders', json={'patientId':'P', 'patientName':'DEMO', 'tests':['GLU']}).status_code, 400)
            self.assertIn('MSA|AR|', mllp.send('127.0.0.1',state['listener']['port'],ORDER))
            self.assertEqual(client.put('/api/design',json=default_design()).status_code,200)
            self.assertEqual(client.get('/api/state').json()['equipment']['state'],'IDLE')
            self.assertEqual(client.post('/api/design',json=default_design()).status_code,405)

    def test_manual_design_builds_real_message_and_is_replaced_in_place(self):
        configuration = default_design()
        configuration['name'] = 'Equipo manual de prueba'
        configuration['segments'][0]['fields'][3]['value'] = 'LIS_PRUEBA'
        configuration['segments'].append({'name':'NTE','repeat':False,'fields':[{'field':1,'source':'fixed','value':'1'}, {'field':3,'source':'fixed','value':'Texto de prueba'}]})
        self.assertEqual(self.client.put('/api/design',json=configuration).status_code,200)
        self.create()
        prepared=self.prepare()
        self.assertIn('|LIS_PRUEBA|',prepared['rawResult'])
        self.assertIn('NTE|1||Texto de prueba',prepared['rawResult'])
        received=[]
        def reject_demo(raw):
            received.append(raw)
            return hl7.ack(raw,DEFAULT_PROFILE,'AE','Prueba de recepción')
        with peer(reject_demo) as port:
            self.destination(port)
            self.assertEqual(self.client.post('/api/orders/MANUAL-1/send',json={}).json()['status'],'REJECTED')
        self.assertEqual(received,[prepared['rawResult']])
        configuration['name']='Mismo diseño actualizado'
        self.assertEqual(self.client.put('/api/design',json=configuration).status_code,200)
        self.assertEqual(self.demo.store.connection.execute('SELECT COUNT(*) FROM design').fetchone()[0],1)
        self.assertEqual(self.state()['orders'][0]['status'],'DRAFT')
        self.assertEqual(self.client.post('/api/orders/MANUAL-1/send',json={}).status_code,400)

    def test_preview_does_not_activate_or_send_and_invalid_design_keeps_previous(self):
        configuration=default_design()
        previous=self.state()['design']
        preview=self.client.post('/api/design/preview',json={'design':configuration})
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertIn('VISTA-PREVIA',preview.json()['raw'])
        self.assertEqual(self.state()['messages'],[])
        self.assertEqual(self.state()['design'],previous)
        configuration['segments']=[s for s in configuration['segments'] if s['name']!='PID']
        self.assertEqual(self.client.put('/api/design',json=configuration).status_code,400)
        self.assertEqual(self.state()['design'],previous)

    def test_duplicate_field_and_fixed_control_id_are_rejected(self):
        configuration=default_design()
        configuration['segments'][1]['fields'].append({'field':3,'component':1,'source':'patient.id','value':''})
        self.assertEqual(self.client.put('/api/design',json=configuration).status_code,400)
        configuration=default_design()
        next(item for item in configuration['segments'][0]['fields'] if item['field']==10).update(source='fixed',value='DUPLICATE')
        self.assertEqual(self.client.put('/api/design',json=configuration).status_code,400)

    def test_edited_visible_values_cannot_send_previous_result(self):
        self.create()
        self.prepare()
        response=self.client.post('/api/orders/MANUAL-1/send',json={'values':{'GLU':'999'}})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.state()['messages'],[])
        self.assertEqual(self.state()['orders'][0]['values']['GLU'],'91.5')

    def test_text_analite_sample_and_study_roundtrip(self):
        self.client.put('/api/catalog',json={'code':'TXT','name':'Texto de prueba','valueType':'ST','unit':'','reference':'Texto libre','flag':'A'})
        response=self.client.post('/api/orders',json={'orderId':'TEXT-ORDER','sampleId':'TUBE-123','studyCode':'PANEL','studyName':'Panel de prueba','patientId':'P','patientName':'DEMO','tests':['TXT']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.post('/api/orders/TEXT-ORDER/load',json={}).json()['status'],'SAMPLE_LOADED')
        self.assertEqual(self.state()['equipment']['state'],'SAMPLE_LOADED')
        raw=self.client.post('/api/orders/TEXT-ORDER/results',json={'values':{'TXT':'Texto | sintético'}}).json()['rawResult']
        self.assertIn('OBR|1|TEXT-ORDER|TUBE-123|PANEL^Panel de prueba',raw)
        self.assertIn('OBX|1|ST|TXT^Texto de prueba||Texto \\F\\ sintético||Texto libre|A|||F',raw)

    def test_received_study_identifier_is_preserved(self):
        mllp.send('127.0.0.1',self.port,ORDER)
        raw=self.prepare('DEMO-ORD-001')['rawResult']
        self.assertIn('OBR|1|DEMO-ORD-001|DEMO-SAMPLE-001|GLU^Glucosa',raw)

    def test_catalog_changes_invalidate_prepared_result(self):
        self.create()
        self.prepare()
        self.client.put('/api/catalog',json={'code':'GLU','name':'Glucosa modificada','unit':'mmol/L'})
        self.assertEqual(self.state()['orders'][0]['status'],'DRAFT')
        self.assertEqual(self.client.post('/api/orders/MANUAL-1/send',json={}).status_code,400)

    def test_preview_can_inspect_any_catalog_analite(self):
        self.client.put('/api/catalog',json={'code':'TXT','name':'Texto de prueba','valueType':'ST','unit':''})
        response=self.client.post('/api/design/preview',json={'design':default_design(),'tests':['TXT']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('OBX|1|ST|TXT^Texto de prueba||DEMO',response.json()['raw'])
        self.assertNotIn('GLU^Glucosa',response.json()['raw'])

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
