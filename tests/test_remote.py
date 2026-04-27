import httpx
import pytest

from keep.config import StoreConfig
from keep.remote import RemoteKeeper


def test_remote_http_error_includes_daemon_request_id(tmp_path):
    keeper = RemoteKeeper("http://localhost:9999", "", StoreConfig(path=tmp_path))
    request = httpx.Request("GET", "http://localhost:9999/v1/notes/missing")
    response = httpx.Response(
        500,
        json={"error": "internal server error", "request_id": "req-remote"},
        request=request,
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        keeper._raise_for_status(response)

    message = str(excinfo.value)
    assert "internal server error" in message
    assert "request_id=req-remote" in message


def test_remote_streaming_http_error_reads_request_id(tmp_path):
    keeper = RemoteKeeper("http://localhost:9999", "", StoreConfig(path=tmp_path))
    request = httpx.Request("GET", "http://localhost:9999/v1/export")
    response = httpx.Response(
        500,
        stream=httpx.ByteStream(b'{"error":"export failed","request_id":"req-stream"}'),
        request=request,
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        keeper._raise_for_status(response)

    message = str(excinfo.value)
    assert "export failed" in message
    assert "request_id=req-stream" in message
