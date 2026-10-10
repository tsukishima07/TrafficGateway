
import json
import socket
import threading
import select
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = Path(__file__).resolve().parent

with (BASE_DIR / "config.json").open(encoding="utf-8") as f:
    CONFIG = json.load(f)

lock = threading.RLock()
next_backend = {}
stats = {}
health = {}
active_connections = 0
recent_events = []
MAX_EVENTS = 30

for route in CONFIG["routes"]:
    next_backend[route["listen_port"]] = 0

    for backend in route["backends"]:
        key = (backend["host"], backend["port"])
        stats.setdefault(key, {
            "connections": 0,
            "bytes": 0
        })
        health[key] = False


def log_event(message):
    timestamp = time.strftime("%H:%M:%S")
    entry = f"{timestamp} | {message}"

    with lock:
        recent_events.insert(0, entry)
        del recent_events[MAX_EVENTS:]

    print(entry)


def check_backend(backend):
    key = (backend["host"], backend["port"])

    try:
        with socket.create_connection(key, timeout=1):
            alive = True
    except OSError:
        alive = False

    changed = False

    with lock:
        if health[key] != alive:
            health[key] = alive
            changed = True

    if changed:
        log_event(
            f"[HEALTH] {key[0]}:{key[1]} "
            f"{'UP' if alive else 'DOWN'}"
        )


def health_monitor():
    while True:
        for route in CONFIG["routes"]:
            for backend in route["backends"]:
                check_backend(backend)

        time.sleep(3)


def choose_backend(route):
    port = route["listen_port"]
    backends = route["backends"]

    with lock:
        start = next_backend[port]
        next_backend[port] += 1

        ordered = [
            backends[(start + i) % len(backends)]
            for i in range(len(backends))
        ]

        available = [
            b for b in ordered
            if health[(b["host"], b["port"])]
        ]

    return available[0] if available else None


def relay(client, backend_socket, key):
    global active_connections

    sockets = [client, backend_socket]

    try:
        while True:
            readable, _, _ = select.select(
                sockets, [], [], 60
            )

            if not readable:
                log_event("[TIMEOUT] Idle connection closed")
                break

            for source in readable:
                destination = (
                    backend_socket if source is client else client
                )

                try:
                    data = source.recv(65536)
                except OSError:
                    return

                if not data:
                    return

                try:
                    destination.sendall(data)
                except OSError:
                    return

                with lock:
                    stats[key]["bytes"] += len(data)

    finally:
        client.close()
        backend_socket.close()

        with lock:
            active_connections -= 1


def handle_client(client, address, route):
    global active_connections

    backend_socket = None
    counted_active = False

    try:
        target = choose_backend(route)

        if target is None:
            log_event(f"[ERROR] No healthy backend for {address}")
            return

        key = (target["host"], target["port"])

        try:
            backend_socket = socket.create_connection(
                key, timeout=2
            )
            backend_socket.settimeout(None)

        except OSError as exc:
            with lock:
                health[key] = False

            log_event(
                f"[ERROR] Backend {key[0]}:{key[1]} "
                f"connection failed: {exc}"
            )
            return

        with lock:
            stats[key]["connections"] += 1
            active_connections += 1
            counted_active = True

        log_event(
            f"[ROUTE] {address} -> {key[0]}:{key[1]}"
        )

        relay(client, backend_socket, key)
        backend_socket = None
        counted_active = False

    except Exception as exc:
        log_event(f"[ERROR] Client {address}: {exc}")

    finally:
        client.close()

        if backend_socket is not None:
            backend_socket.close()

        # If relay was never entered, undo the active count.
        if counted_active:
            with lock:
                active_connections -= 1


def start_route(route):
    port = route["listen_port"]

    with socket.socket(
        socket.AF_INET, socket.SOCK_STREAM
    ) as server:
        server.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )
        server.bind(("0.0.0.0", port))
        server.listen(200)

        log_event(f"[GATEWAY] Listening on port {port}")

        while True:
            client, address = server.accept()

            threading.Thread(
                target=handle_client,
                args=(client, address, route),
                daemon=True
            ).start()


def status_snapshot():
    with lock:
        backends = []

        for route in CONFIG["routes"]:
            for backend in route["backends"]:
                key = (backend["host"], backend["port"])
                item = stats[key]

                backends.append({
                    "name": f"Backend-{backend['port']}",
                    "host": backend["host"],
                    "port": backend["port"],
                    "up": health[key],
                    "connections": item["connections"],
                    "bytes": item["bytes"]
                })

        return {
            "active": active_connections,
            "total": sum(
                item["connections"] for item in stats.values()
            ),
            "bytes": sum(
                item["bytes"] for item in stats.values()
            ),
            "healthy": sum(1 for value in health.values() if value),
            "backends": backends,
            "events": list(recent_events)
        }


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/status":

            body = json.dumps(
                status_snapshot()
            ).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type", "application/json; charset=utf-8"
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Length", str(len(body))
            )
            self.end_headers()
            self.wfile.write(body)

        elif self.path in ("/", "/dashboard.html"):
            try:
                body = (
                    BASE_DIR / "dashboard.html"
                ).read_bytes()

                self.send_response(200)
                self.send_header(
                    "Content-Type", "text/html; charset=utf-8"
                )
                self.send_header(
                    "Content-Length", str(len(body))
                )
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            except OSError:
                self.send_error(
                    500, "dashboard.html not found"
                )

        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


def start_dashboard():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 8080),
        DashboardHandler
    )

    log_event("[UI] Dashboard available at http://127.0.0.1:8080")
    server.serve_forever()


def print_status():
    while True:
        time.sleep(10)

        snapshot = status_snapshot()

        print("\n========== GATEWAY STATUS ==========")
        print(f"Active connections: {snapshot['active']}")

        for backend in snapshot["backends"]:
            state = "UP" if backend["up"] else "DOWN"
            print(
                f"{backend['host']}:{backend['port']} | "
                f"{state} | Connections: {backend['connections']} | "
                f"Bytes: {backend['bytes']}"
            )

        print("====================================\n")


def main():
    threading.Thread(
        target=health_monitor, daemon=True
    ).start()

    threading.Thread(
        target=print_status, daemon=True
    ).start()

    threading.Thread(
        target=start_dashboard, daemon=True
    ).start()

    for route in CONFIG["routes"]:
        threading.Thread(
            target=start_route,
            args=(route,),
            daemon=True
        ).start()

    print("[GATEWAY] Monitoring enabled")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("Gateway stopped")


if __name__ == "__main__":
    main()
