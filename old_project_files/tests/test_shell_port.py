
import socket
from hfagent.tools.shell import RunCommand, _guess_server_port

def test_guess_ports():
    assert _guess_server_port("npm start") == 3000
    assert _guess_server_port("vite --port 5174") == 5174

def test_skip_when_port_open():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.listen(1)
    try:
        out = RunCommand().run(f"npm start -- --port {port}", background=True)
        assert "SKIPPED start" in out
        assert "Do NOT start another server" in out
    finally:
        s.close()
