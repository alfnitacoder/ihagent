"""Connection and HTTP errors name the URL and the cause."""

from hfagent.agent import format_api_error, is_tool_choice_unsupported
from hfagent.errors import classify_api_failure, is_connection_failure


VLLM_TOOL_400 = (
    "Error code: 400 - {'error': 'auto tool choice requires "
    "--enable-auto-tool-choice and --tool-call-parser to be set', "
    "'type': 'BadRequestError'}"
)


def _status(code, message=""):
    exc = Exception(message or f"Error code: {code}")
    exc.status_code = code
    return exc


def _caused(message, cause):
    exc = Exception(message)
    exc.__cause__ = cause
    return exc


def test_plain_and_tool_choice_messages_stay():
    assert format_api_error(RuntimeError("boom")) == "boom"
    text = format_api_error(Exception(VLLM_TOOL_400))
    assert is_tool_choice_unsupported(Exception(VLLM_TOOL_400))
    assert "Retrying in text-tool mode" in text
    assert "BadRequestError" not in text


def test_connection_error_names_url_and_tunnel():
    text = format_api_error(
        Exception("Connection error."),
        base_url="https://abc.trycloudflare.com/v1",
    )
    assert "https://abc.trycloudflare.com/v1" in text
    assert "Cloudflare 530 / connect error" in text
    assert is_connection_failure(Exception("Connection error."))


def test_dns_and_tls_are_distinct():
    dns = _caused("Connection error.", OSError("Temporary failure in name resolution"))
    tls = _caused("Connection error.", OSError("CERTIFICATE_VERIFY_FAILED"))
    assert classify_api_failure(dns) == "dns"
    assert classify_api_failure(tls) == "tls"
    dns_text = format_api_error(dns, base_url="https://tunnel.example/v1")
    tls_text = format_api_error(tls, base_url="https://tunnel.example/v1")
    assert "DNS failure" in dns_text
    assert "TLS failure" in tls_text
    assert "https://tunnel.example/v1" in dns_text
    assert is_connection_failure(dns)
    assert is_connection_failure(tls)


def test_timeout_cloudflare_530_and_gateway():
    class APITimeoutError(Exception):
        pass

    timeout = APITimeoutError("Request timed out.")
    tunnel = _status(530, "cloudflare error code: 1033")
    gateway = _status(502, "bad gateway")
    assert classify_api_failure(timeout) == "timeout"
    assert classify_api_failure(tunnel) == "tunnel"
    assert classify_api_failure(gateway) == "gateway"
    assert "timeout" in format_api_error(timeout, base_url="https://host/v1")
    assert "530" in format_api_error(tunnel, base_url="https://host/v1")
    assert "502" in format_api_error(gateway, base_url="https://host/v1")
    assert is_connection_failure(timeout)
    assert is_connection_failure(tunnel)
    assert is_connection_failure(gateway)


def test_auth_suggests_key_var_and_does_not_count_as_unreachable():
    exc = _status(401, "Error code: 401 - invalid api key")
    text = format_api_error(
        exc,
        base_url="https://abc.trycloudflare.com/v1",
        key_var="HFAGENT_API_KEY",
    )
    assert "https://abc.trycloudflare.com/v1" in text
    assert "401/403 auth" in text
    assert "HFAGENT_API_KEY" in text
    assert not is_connection_failure(exc)
    forbidden = _status(403)
    assert classify_api_failure(forbidden) == "auth"
    assert "401/403 auth" in format_api_error(forbidden, base_url="https://host/v1")


def test_404_names_the_model():
    exc = _status(404, "Error code: 404 - model not found")
    text = format_api_error(
        exc,
        base_url="https://host/v1",
        model="qwen3.8-27b-uncensored-mtp",
    )
    assert "404 model not found" in text
    assert "qwen3.8-27b-uncensored-mtp" in text
    assert "https://host/v1" in text
    assert not is_connection_failure(exc)


def test_5xx_names_the_server():
    exc = _status(500, "Error code: 500 - internal")
    text = format_api_error(exc, base_url="https://host/v1")
    assert "5xx" in text
    assert "HTTP 500" in text
    assert "https://host/v1" in text
    assert not is_connection_failure(exc)
