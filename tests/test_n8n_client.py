import io
import json
import urllib.error

import pytest

from n8n_client import N8NClient, N8NError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def test_list_workflows_success():
    def opener(request, timeout):
        return Response(json.dumps({"data": [{"id": "1", "name": "Render"}]}).encode())

    client = N8NClient("http://n8n/api/v1", "key", opener=opener)

    assert client.list_workflows() == [{"id": "1", "name": "Render"}]


@pytest.mark.parametrize("status", [401, 404])
def test_http_errors_are_normalized(status):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(b'{"message":"denied"}'))

    client = N8NClient("http://n8n/api/v1", "key", opener=opener)
    with pytest.raises(N8NError) as error:
        client.list_workflows()
    assert error.value.status == status


def test_timeout_is_normalized():
    def opener(request, timeout):
        raise TimeoutError("late")

    client = N8NClient("http://n8n/api/v1", "key", opener=opener)
    with pytest.raises(N8NError, match="unavailable"):
        client.list_workflows()


def test_malformed_json_is_rejected():
    client = N8NClient("http://n8n/api/v1", "key", opener=lambda request, timeout: Response(b"not-json"))
    with pytest.raises(N8NError, match="malformed"):
        client.list_workflows()
