"""Bounded MLLP framing, including fragmented and consecutive frames."""
import socket
import socketserver
import time

START, END = b"\x0b", b"\x1c\r"
MAX_FRAME = 256 * 1024


class Frames:
    def __init__(self):
        self.buffer = b""

    def feed(self, chunk):
        self.buffer += chunk
        messages = []
        while self.buffer:
            if not self.buffer.startswith(START):
                raise ValueError("Falta el inicio de bloque MLLP (0x0b).")
            end = self.buffer.find(END, 1)
            if end < 0:
                if len(self.buffer) > MAX_FRAME + 3:
                    raise ValueError("Mensaje MLLP demasiado grande.")
                break
            if end - 1 > MAX_FRAME:
                raise ValueError("Mensaje MLLP demasiado grande.")
            messages.append(self.buffer[1:end].decode("utf-8"))
            self.buffer = self.buffer[end + len(END):]
        return messages


def send(host, port, raw, timeout=5):
    parser = Frames()
    deadline = time.monotonic() + timeout
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.sendall(START + raw.encode("utf-8") + END)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Tiempo agotado esperando ACK.")
            connection.settimeout(remaining)
            chunk = connection.recv(8192)
            if not chunk:
                raise ConnectionError("El destino cerró la conexión sin ACK completo.")
            messages = parser.feed(chunk)
            if messages:
                return messages[0]


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        parser = Frames()
        self.request.settimeout(15)
        started = time.monotonic()
        try:
            while True:
                # A partial frame has a deadline even if a peer keeps sending.
                remaining = 15 - (time.monotonic() - started)
                if remaining <= 0:
                    return
                self.request.settimeout(remaining)
                chunk = self.request.recv(8192)
                if not chunk:
                    return
                messages = parser.feed(chunk)
                for raw in messages:
                    reply = self.server.receive(raw)
                    if reply:
                        self.request.sendall(START + reply.encode("utf-8") + END)
                if messages:
                    started = time.monotonic()
        except (OSError, ValueError, UnicodeError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = False

    def __init__(self, address, receive):
        self.receive = receive
        super().__init__(address, Handler)
