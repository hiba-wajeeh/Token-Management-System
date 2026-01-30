import socket, json, threading, time

DISCOVERY_PORT = 9999

def _get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't need to be reachable; just used to pick correct interface
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def start_broadcast(http_port: int, interval: float = 3.0):
    ip = _get_local_ip()

    payload = {
        "service": "Reception-QMS",
        "port": http_port,
        "ip": ip
    }

    def _loop():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        while True:
            try:
                sock.sendto(json.dumps(payload).encode("utf-8"), ("255.255.255.255", DISCOVERY_PORT))
            except Exception:
                pass
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return payload
