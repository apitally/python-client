from __future__ import annotations

import logging
import queue
import time
from functools import partial
from threading import Event, Thread
from typing import Any, Callable, Dict, Optional, Tuple

import backoff
import requests

from apitally.client.base import MAX_QUEUE_TIME, REQUEST_TIMEOUT, ApitallyClientBase
from apitally.client.logging import get_logger


logger = get_logger(__name__)
retry = partial(
    backoff.on_exception,
    backoff.expo,
    requests.RequestException,
    max_tries=3,
    logger=logger,
    giveup_log_level=logging.WARNING,
)


# Function to register an on-exit callback for both Python and IPython runtimes
try:

    def register_exit(func: Callable[..., Any], *args, **kwargs) -> Callable[..., Any]:  # pragma: no cover
        def callback():
            func()
            ipython.events.unregister("post_execute", callback)

        ipython.events.register("post_execute", callback)
        return func

    ipython = get_ipython()  # type: ignore
except NameError:
    from atexit import register as register_exit


class ApitallyClient(ApitallyClientBase):
    def __init__(self, client_id: str, env: str) -> None:
        super().__init__(client_id=client_id, env=env)
        self._thread: Optional[Thread] = None
        self._stop_sync_loop = Event()
        self._requests_data_queue: queue.Queue[Tuple[float, Dict[str, Any]]] = queue.Queue()

    def start_sync_loop(self) -> None:
        self._stop_sync_loop.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = Thread(target=self._run_sync_loop, daemon=True)
            self._thread.start()
            register_exit(self.stop_sync_loop)

    def _run_sync_loop(self) -> None:
        try:
            last_sync_time = 0.0
            while not self._stop_sync_loop.is_set():
                try:
                    now = time.time()
                    if (now - last_sync_time) >= self.sync_interval:
                        with requests.Session() as session:
                            if not self._app_info_sent and last_sync_time > 0:  # not on first sync
                                self.send_app_info(session)
                            self.send_requests_data(session)
                        last_sync_time = now
                    time.sleep(1)
                except Exception as e:  # pragma: no cover
                    logger.exception(e)
        finally:
            # Send any remaining requests data before exiting
            with requests.Session() as session:
                self.send_requests_data(session)

    def stop_sync_loop(self) -> None:
        self._stop_sync_loop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def set_app_info(self, app_info: Dict[str, Any]) -> None:
        self._app_info_sent = False
        self._app_info_payload = self.get_info_payload(app_info)
        with requests.Session() as session:
            self.send_app_info(session)

    def send_app_info(self, session: requests.Session) -> None:
        if self._app_info_payload is not None:
            self._send_app_info(session, self._app_info_payload)

    def send_requests_data(self, session: requests.Session) -> None:
        payload = self.get_requests_payload()
        self._requests_data_queue.put_nowait((time.time(), payload))

        failed_items = []
        while not self._requests_data_queue.empty():
            payload_time, payload = self._requests_data_queue.get_nowait()
            try:
                if (time_offset := time.time() - payload_time) <= MAX_QUEUE_TIME:
                    payload["time_offset"] = time_offset
                    self._send_requests_data(session, payload)
                self._requests_data_queue.task_done()
            except requests.RequestException:
                failed_items.append((payload_time, payload))
        for item in failed_items:
            self._requests_data_queue.put_nowait(item)

    @retry(raise_on_giveup=False)
    def _send_app_info(self, session: requests.Session, payload: Dict[str, Any]) -> None:
        logger.debug("Sending app info")
        response = session.post(url=f"{self.hub_url}/info", json=payload, timeout=REQUEST_TIMEOUT)
        self._handle_hub_response(response)
        self._app_info_sent = True
        self._app_info_payload = None

    @retry()
    def _send_requests_data(self, session: requests.Session, payload: Dict[str, Any]) -> None:
        logger.debug("Sending requests data")
        response = session.post(url=f"{self.hub_url}/requests", json=payload, timeout=REQUEST_TIMEOUT)
        self._handle_hub_response(response)

    def _handle_hub_response(self, response: requests.Response) -> None:
        if response.status_code == 404:
            self.stop_sync_loop()
            logger.error(f"Invalid Apitally client ID {self.client_id}")
        else:
            response.raise_for_status()
