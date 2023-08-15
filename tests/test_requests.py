from __future__ import annotations

from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from apitally.requests import RequestLogger


@pytest.fixture()
def requests() -> RequestLogger:
    from apitally.requests import RequestLogger

    requests = RequestLogger()
    requests.log_request(
        method="GET",
        path="/test",
        status_code=200,
        response_time=0.105,
    )
    requests.log_request(
        method="GET",
        path="/test",
        status_code=200,
        response_time=0.227,
    )
    return requests


async def test_get_and_reset_requests(requests: RequestLogger):
    assert len(requests.request_count) > 0

    data = requests.get_and_reset_requests()
    assert len(requests.request_count) == 0
    assert len(data) == 1
    assert data[0]["method"] == "GET"
    assert data[0]["path"] == "/test"
    assert data[0]["status_code"] == 200
    assert data[0]["request_count"] == 2
    assert data[0]["response_times"][100] == 1
    assert data[0]["response_times"][220] == 1
