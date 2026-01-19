"""Discovery of E27 panels."""

from __future__ import annotations

import json
import asyncio
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)


@dataclass
class E27System:
    """An ELKE27 panel identity record."""

    panel_mac: str
    panel_host: str
    panel_name: str
    panel_serial: str | None
    port: int
    tls_port: int


def create_udp_socket(discovery_port: int) -> socket.socket:
    """Create a udp socket used for communicating with the device."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    sock.setblocking(False)
    return sock


class ELKDiscovery(asyncio.DatagramProtocol):
    """Discovery main class."""

    def __init__(
        self,
        destination: tuple[str, int],
        on_response: Callable[[bytes, tuple[str, int]], None],
    ) -> None:
        self.transport = None
        self.destination = destination
        self.on_response = on_response

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Trigger on_response."""
        self.on_response(data, addr)

    def error_received(self, exc: Exception | None) -> None:
        """Handle error."""
        _LOGGER.error("ELKDiscovery error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Do nothing on connection lost."""


def _decode_data(raw_response: bytes) -> E27System | None:
    """Decode an ELK discovery response packet."""

    try:
        data = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        _LOGGER.debug("Failed to decode discovery response: %s", e)
        return None

    mac_address = str(data.get("MAC_ADDR", "")).strip().lower()
    panel_host = str(data.get("IPV4_ADDR", "")).strip()
    port = int(data.get("LISTEN_PORT", 0))
    tls_port = int(data.get("ENCRYPTED_LISTEN_PORT", 0))
    panel_name = str(data.get("NAME", "")).strip()
    panel_serial = data.get("SERIAL")
    if panel_serial is not None:
        panel_serial = str(panel_serial).strip()

    return E27System(
        panel_mac=mac_address,
        panel_host=panel_host,
        panel_name=panel_name,
        panel_serial=panel_serial or None,
        port=port,
        tls_port=tls_port,
    )


class AIOELKDiscovery:
    """A 30303 discovery scanner."""

    DISCOVERY_PORT = 2362
    BROADCAST_FREQUENCY = 3
    DISCOVER_MESSAGE = b"{ \"FIND\": \"ELKWCID\" }"
    BROADCAST_ADDRESS = "<broadcast>"

    def __init__(self) -> None:
        self.found_devices: list[E27System] = []

    def _destination_from_address(self, address: str | None) -> tuple[str, int]:
        if address is None:
            address = self.BROADCAST_ADDRESS
        return (address, self.DISCOVERY_PORT)

    def _process_response(
        self,
        data: bytes | None,
        from_address: tuple[str, int],
        address: str | None,
        response_list: dict[tuple[str, int], E27System],
    ) -> bool:
        """Process a response.

        Returns True if processing should stop
        """
        if (
            data is None
            or data == self.DISCOVER_MESSAGE
            or not (b"ELKWC2017" in data)
        ):
            return False
        try:
            response_list[from_address] = _decode_data(data)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Failed to decode response from %s: %s", from_address, ex)
            return False
        return from_address[0] == address

    async def _async_run_scan(
        self,
        transport: asyncio.DatagramTransport,
        destination: tuple[str, int],
        timeout: int,
        found_all_future: asyncio.Future[bool],
    ) -> None:
        """Send the scans."""
        _LOGGER.debug("discover: %s => %s", destination, self.DISCOVER_MESSAGE)
        transport.sendto(self.DISCOVER_MESSAGE, destination)
        quit_time = time.monotonic() + timeout
        remain_time = float(timeout)
        while True:
            time_out = min(remain_time, timeout / self.BROADCAST_FREQUENCY)
            if time_out <= 0:
                return
            try:
                await asyncio.wait_for(
                    asyncio.shield(found_all_future), timeout=time_out
                )
            except TimeoutError:
                if time.monotonic() >= quit_time:
                    return
                # No response, send broadcast again in cast it got lost
                _LOGGER.debug("discover: %s => %s", destination, self.DISCOVER_MESSAGE)
                transport.sendto(self.DISCOVER_MESSAGE, destination)
            else:
                return  # found_all
            remain_time = quit_time - time.monotonic()

    async def async_scan(
        self,
        timeout: int = 10,
        address: str | None = None,
        *,
        sock: socket.socket | None = None,
        socket_factory: Callable[[], socket.socket] | None = None,
    ) -> list[E27System]:
        """Discover ELK devices."""
        if sock is None:
            sock = socket_factory() if socket_factory is not None else create_udp_socket(self.DISCOVERY_PORT)
        destination = self._destination_from_address(address)
        found_all_future: asyncio.Future[bool] = asyncio.Future()
        response_list: dict[tuple[str, int], E27System] = {}

        def _on_response(data: bytes, addr: tuple[str, int]) -> None:
            _LOGGER.debug("discover: %s <= %s", addr, data)
            if self._process_response(data, addr, address, response_list):
                found_all_future.set_result(True)

        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: ELKDiscovery(
                destination=destination,
                on_response=_on_response,
            ),
            sock=sock,
        )
        try:
            await self._async_run_scan(
                transport,
                destination,
                timeout,
                found_all_future,
            )
        finally:
            transport.close()

        self.found_devices = list(response_list.values())
        return self.found_devices
