import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from tuxman import updates


@pytest.mark.parametrize("tag,status", [("v0.2.0", updates.UPDATE_AVAILABLE), ("v0.1.0", updates.UP_TO_DATE), ("v0.0.9", updates.UP_TO_DATE)])
def test_release_comparison(tag, status):
    response = io.BytesIO(json.dumps({"tag_name": tag}).encode())
    with patch("urllib.request.urlopen", return_value=response):
        result = updates.check_for_update("0.1.0")
    assert result.status == status
    assert result.url == updates.RELEASES_URL


@pytest.mark.parametrize("payload", [b"invalid", b"[]", b'{"tag_name": 42}'])
def test_invalid_responses(payload):
    with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)):
        assert updates.check_for_update("0.1.0").status == updates.ERROR


@pytest.mark.parametrize("error,status", [
    (HTTPError("https://api.github.com", 404, "missing", {}, None), updates.NO_RELEASES),
    (HTTPError("https://api.github.com", 429, "limited", {}, None), updates.ERROR),
    (URLError("offline"), updates.ERROR),
])
def test_network_failures(error, status):
    with patch("urllib.request.urlopen", side_effect=error):
        assert updates.check_for_update("0.1.0").status == status
