import websockets
from websockets.http11 import Request

from cabbagecode.remote import RemoteServer


def _status(server, path):
    resp = server._process_http_request(None, Request(path, websockets.Headers()))
    return None if resp is None else resp.status_code


def test_remote_defaults_to_localhost_with_a_token():
    server = RemoteServer(providers=[])
    assert server.addr == "127.0.0.1"
    assert len(server.token) >= 24


def test_remote_requires_token_for_page_and_websocket():
    server = RemoteServer(providers=[], token="t0k")
    assert _status(server, "/") == 401
    assert _status(server, "/?token=bad") == 401
    assert _status(server, "/?token=t0k") == 200
    assert _status(server, "/ws") == 401
    assert _status(server, "/ws?token=t0k") is None  # 放行，继续 WebSocket 升级
    assert _status(server, "/other?token=t0k") == 404
