from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
import requests
from pytest_mock import MockerFixture
from requests_mock import Mocker


if TYPE_CHECKING:
    from apitally.client.threading import ApitallyClient


CLIENT_ID = "76b5cb91-a0a4-4ea0-a894-57d2b9fcb2c9"
ENV = "default"


@pytest.fixture(scope="module")
def client() -> ApitallyClient:
    from apitally.client.base import ApitallyKeyCacheBase
    from apitally.client.threading import ApitallyClient

    class ApitallyKeyCache(ApitallyKeyCacheBase):
        cache: dict[str, str] = {}

        def store(self, data: str) -> None:
            self.cache[self.cache_key] = data

        def retrieve(self) -> str | None:
            return self.cache.get(self.cache_key)

    client = ApitallyClient(
        client_id=CLIENT_ID,
        env=ENV,
        sync_api_keys=True,
        key_cache_class=ApitallyKeyCache,
    )
    client.request_logger.log_request(
        consumer=None,
        method="GET",
        path="/test",
        status_code=200,
        response_time=0.105,
    )
    client.request_logger.log_request(
        consumer=None,
        method="GET",
        path="/test",
        status_code=200,
        response_time=0.227,
    )
    client.request_logger.log_request(
        consumer=None,
        method="GET",
        path="/test",
        status_code=422,
        response_time=0.02,
    )
    client.validation_error_logger.log_validation_errors(
        consumer=None,
        method="GET",
        path="/test",
        detail=[
            {
                "loc": ["query", "foo"],
                "type": "type_error.integer",
                "msg": "value is not a valid integer",
            },
        ],
    )
    return client


def test_sync_loop(client: ApitallyClient, mocker: MockerFixture):
    send_requests_data_mock = mocker.patch("apitally.client.threading.ApitallyClient.send_requests_data")
    get_keys_mock = mocker.patch("apitally.client.threading.ApitallyClient.get_keys")
    mocker.patch.object(client, "sync_interval", 0.05)

    client.start_sync_loop()
    time.sleep(0.02)  # Ensure loop enters first iteration
    client.stop_sync_loop()  # Should stop after first iteration
    assert client._thread is None
    assert send_requests_data_mock.call_count >= 1
    assert get_keys_mock.call_count >= 1


def test_send_requests_data(client: ApitallyClient, requests_mock: Mocker):
    from apitally.client.base import HUB_BASE_URL, HUB_VERSION

    mock = requests_mock.register_uri("POST", f"{HUB_BASE_URL}/{HUB_VERSION}/{CLIENT_ID}/{ENV}/requests")
    with requests.Session() as session:
        client.send_requests_data(session)

    assert len(mock.request_history) == 1
    request_data = mock.request_history[0].json()
    assert len(request_data["requests"]) == 2
    assert request_data["requests"][0]["request_count"] == 2
    assert len(request_data["validation_errors"]) == 1
    assert request_data["validation_errors"][0]["error_count"] == 1


def test_set_app_info(client: ApitallyClient, requests_mock: Mocker):
    from apitally.client.base import HUB_BASE_URL, HUB_VERSION

    mock = requests_mock.register_uri("POST", f"{HUB_BASE_URL}/{HUB_VERSION}/{CLIENT_ID}/{ENV}/info")
    app_info = {"paths": [], "client_version": "1.0.0", "starlette_version": "0.28.0", "python_version": "3.11.4"}
    client.set_app_info(app_info)

    assert len(mock.request_history) == 1
    request_data = mock.request_history[0].json()
    assert request_data["paths"] == []
    assert request_data["client_version"] == "1.0.0"


def test_get_keys(client: ApitallyClient, requests_mock: Mocker):
    from apitally.client.base import HUB_BASE_URL, HUB_VERSION

    mock = requests_mock.register_uri(
        "GET",
        f"{HUB_BASE_URL}/{HUB_VERSION}/{CLIENT_ID}/{ENV}/keys",
        json={"salt": "x", "keys": {"x": {"key_id": 1, "api_key_id": 1, "expires_in_seconds": None}}},
    )
    with requests.Session() as session:
        client.get_keys(session)

    assert len(mock.request_history) == 1
    assert len(client.key_registry.keys) == 1

    assert client.key_cache is not None
    cache_result = client.key_cache.retrieve()
    assert cache_result is not None
    assert json.loads(cache_result)["salt"] == "x"
