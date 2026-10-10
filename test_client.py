
import socket
import threading


def test_client(number):
    try:
        with socket.create_connection(
            ("127.0.0.1", 8000),
            timeout=5
        ) as client:
            message = f"Hello from client {number}".encode()
            client.sendall(message)

            response = client.recv(4096)
            print(response.decode())

    except OSError as exc:
        print(f"Client {number} failed: {exc}")


threads = []

for i in range(10):
    thread = threading.Thread(target=test_client, args=(i,))
    thread.start()
    threads.append(thread)

for thread in threads:
    thread.join()
