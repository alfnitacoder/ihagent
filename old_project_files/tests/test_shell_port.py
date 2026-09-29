
import socket
from hfagent.tools.shell import RunCommand, _guess_server_port, foreign_shell_reason

def test_windows_rejects_unix_ip_commands():
    reason = foreign_shell_reason("ifconfig", "Windows")
    assert reason and "Windows" in reason and "ipconfig" in reason
    assert foreign_shell_reason("ip addr", "Windows")
    assert foreign_shell_reason("ipconfig getifaddr en0", "Windows")
    assert foreign_shell_reason("ipconfig", "Windows") is None
    assert foreign_shell_reason("ipconfig /all", "Windows") is None


def test_macos_rejects_linux_and_windows_ip_commands():
    assert foreign_shell_reason("ip addr show", "Darwin")
    assert foreign_shell_reason("hostname -I", "Darwin")
    assert foreign_shell_reason("ipconfig /all", "Darwin")
    assert foreign_shell_reason("ipconfig getifaddr en0", "Darwin") is None
    assert foreign_shell_reason("ifconfig", "Darwin") is None


def test_linux_rejects_other_os_ip_commands():
    assert foreign_shell_reason("ipconfig", "Linux")
    assert foreign_shell_reason("sw_vers", "Linux")
    assert foreign_shell_reason("hostname -I", "Linux") is None
    assert foreign_shell_reason("ip addr", "Linux") is None


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
