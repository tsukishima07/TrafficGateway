
import socket
import sys
import threading

HOST = "127.0.0.1"
PORT = int(sys.argv[1])
NAME = sys.argv[2]


def handle_client(conn, address):
    with conn:
        print(f"[{NAME}] Client connected: {address}")

        try:
            while True:
                data = conn.recv(4096)

                if not data:
                    break

                print(f"[{NAME}] Received: {data!r}")
                conn.sendall(f"{NAME} received: ".encode() + data)

        except OSError as exc:
            print(f"[{NAME}] Connection error: {exc}")

        print(f"[{NAME}] Client disconnected")


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen(100)

print(f"{NAME} listening on {HOST}:{PORT}")

while True:
    conn, address = server.accept()
    threading.Thread(
        target=handle_client,
        args=(conn, address),
        daemon=True
    ).start()
