import socket

def test_port(host, port, timeout=5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        print(f"SUCCESS: Connected to {host}:{port}")
        return True
    except Exception as e:
        print(f"FAILED: Cannot connect to {host}:{port} -> {e}")
        return False
    finally:
        s.close()

# Example usage
test_port("collector.internal", 4318)   # HTTP OTLP
test_port("collector.internal", 4317)   # gRPC OTLP
