"""Module that implements SSDP protocol."""

from __future__ import annotations

import logging
import re
import select
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta
from wsgiref.handlers import format_date_time

from .ouimeaux_device.api.long_press import VIRTUAL_DEVICE_UDN
from .util import get_callback_address, interface_addresses

DISCOVER_TIMEOUT = 5

LOG = logging.getLogger(__name__)

RESPONSE_REGEX = re.compile(r"\n(.*)\: (.*)\r")

MIN_TIME_BETWEEN_SCANS = timedelta(seconds=59)

MULTICAST_GROUP = "239.255.255.250"
MULTICAST_PORT = 1900

# Wemo specific urn:
ST = "urn:Belkin:service:basicevent:1"
VIRTUAL_DEVICE_USN = f"{VIRTUAL_DEVICE_UDN}::{ST}"
MAX_AGE = 86400

SSDP_REPLY = f"""HTTP/1.1 200 OK
CACHE-CONTROL: max-age={MAX_AGE}
DATE: %(date)s
EXT:
LOCATION: http://%(callback)s/setup.xml
OPT: "http://schemas.upnp.org/upnp/1/0/"; ns=01
01-NLS: %(nls)s
SERVER: Unspecified, UPnP/1.0, Unspecified
X-User-Agent: pywemo
ST: {ST}
USN: {VIRTUAL_DEVICE_USN}

"""  # Newline characters at the the end of SSDP_REPLY are intentional.
SSDP_REPLY = SSDP_REPLY.replace("\n", "\r\n")

SSDP_NOTIFY = f"""NOTIFY * HTTP/1.1
HOST: {MULTICAST_GROUP}:{MULTICAST_PORT}
CACHE-CONTROL: max-age={MAX_AGE}
LOCATION: http://%(callback)s/setup.xml
OPT: "http://schemas.upnp.org/upnp/1/0/"; ns=01
01-NLS: %(nls)s
NT: {ST}
NTS: %(nts)s
SERVER: Unspecified, UPnP/1.0, Unspecified
X-User-Agent: pywemo
USN: {VIRTUAL_DEVICE_USN}

"""  # Newline characters at the the end of SSDP_NOTIFY are intentional.
SSDP_NOTIFY = SSDP_NOTIFY.replace("\n", "\r\n")

EXPECTED_ST_HEADER = ("ST: " + ST).encode("UTF-8")
EXPECTED_MAN_HEADER = b'MAN: "ssdp:discover"'


class UPNPEntry:
    """Found uPnP entry."""

    def __init__(self, values: dict[str, str]) -> None:
        """Create a UPNPEntry object."""
        self.values = values
        self._created = datetime.now()
        self._expires: datetime | None = None

        if "cache-control" in self.values:
            cache_seconds = int(self.values["cache-control"].split("=")[1])

            self._expires = self._created + timedelta(seconds=cache_seconds)

    @property
    def st(self) -> str | None:  # pylint: disable=invalid-name
        """Return ST value."""
        return self.values.get("st")

    @property
    def location(self) -> str | None:
        """Return location value."""
        return self.values.get("location")

    @property
    def usn(self) -> str | None:
        """Return unique service name."""
        return self.values.get("usn")

    @property
    def udn(self) -> str:
        """Return unique device name."""
        usn = self.usn or ""
        return usn.split("::")[0]

    @classmethod
    def from_response(cls, response: str) -> UPNPEntry:
        """Create a uPnP entry from a response."""
        return UPNPEntry(
            {
                key.lower(): item
                for key, item in RESPONSE_REGEX.findall(response)
            }
        )

    @property
    def _key(self) -> tuple[str, str | None]:
        """Tuple of values that uniquely identify the UPNPEntry instance."""
        return (self.udn, self.location)

    def __eq__(self, other: object) -> bool:
        """Equality operator."""
        return isinstance(other, type(self)) and self._key == other._key

    def __hash__(self) -> int:
        """Generate hash of instance."""
        return hash(("UPNPEntry", self._key))

    def __repr__(self) -> str:
        """Return the string representation of the object."""
        st = self.st or ""  # pylint: disable=invalid-name
        location = self.location or ""
        udn = self.udn or ""
        return f"<UPNPEntry {st} - {location} - {udn}>"


def build_ssdp_request(ssdp_st: str, ssdp_mx: int) -> bytes:
    """Build the standard request to send during SSDP discovery."""
    return "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            f"ST: {ssdp_st}",
            f"MX: {ssdp_mx}",
            'MAN: "ssdp:discover"',
            f"HOST: {MULTICAST_GROUP}:{MULTICAST_PORT}",
            "",
            "",
        ]
    ).encode("ascii")


def scan(  # pylint: disable=too-many-branches,too-many-locals
    st: str = ST,  # pylint: disable=invalid-name
    timeout: float = DISCOVER_TIMEOUT,
    max_entries: int | None = None,
    match_udn: str | None = None,
) -> list[UPNPEntry]:
    """
    Send a message over the network to discover upnp devices.

    Inspired by Crimsdings ChromeCast code
    https://github.com/crimsdings/  [ChromeCast repository since removed]
    """
    # pylint: disable=too-many-nested-blocks
    ssdp_target = (MULTICAST_GROUP, MULTICAST_PORT)

    entries: list[UPNPEntry] = []

    calc_now = datetime.now

    ssdp_request = build_ssdp_request(st, ssdp_mx=1)
    sockets = []
    try:
        for addr in interface_addresses():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((addr, 0))
                sock.sendto(ssdp_request, ssdp_target)
                sockets.append(sock)
            except OSError:
                pass
            finally:
                if sock not in sockets:
                    sock.close()

        start = calc_now()
        while sockets:
            time_diff = calc_now() - start

            seconds_left = max(timeout - time_diff.seconds, 0)

            ready = select.select(sockets, [], [], min(1, seconds_left))[0]
            if not ready:
                # Only check for timeout when there are no more results. Exit
                # if the time has expired, or probe again if there is more
                # time remaining.
                if seconds_left <= 0:
                    return entries
                for sock in sockets:
                    sock.sendto(ssdp_request, ssdp_target)

            for sock in ready:
                response = sock.recv(1024).decode("UTF-8", "replace")

                entry = UPNPEntry.from_response(response)
                if entry.usn == VIRTUAL_DEVICE_USN:
                    continue  # Don't return the virtual device.

                # Search for devices
                if entry not in entries:
                    if match_udn is None:
                        entries.append(entry)
                    elif match_udn == entry.udn:
                        entries.append(entry)

                    # Return if we've found the max number of devices
                    if max_entries and len(entries) == max_entries:
                        return entries
    except OSError:
        LOG.exception("Socket error while discovering SSDP devices")
    finally:
        for sock in sockets:
            sock.close()

    return entries


class DiscoveryResponder:
    """Inform Wemo devices of the pywemo virtual Wemo device.

    The DiscoveryResponder informs Wemo devices of the /setup.xml URL for the
    pywemo virtual Wemo device. The virtual device is used for receiving long
    press actions from Wemo devices and is integrated into the
    SubscriptionRegistry HTTP server.

    Wemo devices are informed of the pywemo virtual Wemo device in two ways:

    1. Wemo devices periodically send UPnP M-SEARCH discovery requests for the
    to locate other devices on the network. DiscoveryResponder responds to
    these requests with the URL for the virtual device.

    2. A UPnP NOTIFY message is periodically multicasted by DiscoveryResponder
    to inform Wemo devices on the network of the URL for the virtual device.
    """

    def __init__(self, callback_port: int) -> None:
        """Create a server that will respond to WeMo discovery requests.

        Args:
            callback_port: The port for the SubscriptionRegistry HTTP server.
        """
        self.callback_port = callback_port
        self._thread: threading.Thread | None = None
        self._exit = threading.Event()
        self._thread_exception: Exception | None = None
        self._notify_enabled = True  # Only ever set to False in tests.
        self._nls_uuid = str(uuid.uuid4())

    def send_notify(self, nts: str) -> None:
        """Send a UPnP NOTIFY message containing the virtual device URL."""
        ssdp_target = (MULTICAST_GROUP, MULTICAST_PORT)
        for addr in interface_addresses():  # Send on all interfaces.
            params = {
                "callback": get_callback_address(addr, self.callback_port),
                "nls": self._nls_uuid,
                "nts": nts,
            }
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((addr, 0))
                sock.sendto(
                    (SSDP_NOTIFY % params).encode("UTF-8"), ssdp_target
                )
            except OSError:
                pass
            finally:
                sock.close()

    def respond_to_discovery(self) -> None:
        """Respond to a WeMo discovery request with the virtual device URL."""
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Join the multicast group on all interfaces.
            group = socket.inet_aton(MULTICAST_GROUP)
            for addr in interface_addresses():
                try:
                    local = socket.inet_aton(addr)
                    recv_sock.setsockopt(
                        socket.IPPROTO_IP,
                        socket.IP_ADD_MEMBERSHIP,
                        group + local,
                    )
                except OSError as err:
                    LOG.error(
                        "Failed join multicast group on %s: %s", addr, err
                    )

            recv_sock.bind((MULTICAST_GROUP, MULTICAST_PORT))

            next_notify = datetime.min
            while not self._exit.is_set():
                # Periodically send NOTIFY messages.
                now = datetime.now()
                if now > next_notify and self._notify_enabled:
                    next_notify = now + timedelta(seconds=(MAX_AGE / 2) - 30)
                    self.send_notify("ssdp:alive")

                # Check for new discovery requests.
                if not select.select([recv_sock], [], [], 1)[0]:
                    continue  # Timeout, no data. Loop again and check for exit
                msg, sock_addr = recv_sock.recvfrom(1024)
                lines = msg.splitlines()
                if len(lines) < 3 or not lines[0].startswith(
                    b"M-SEARCH * HTTP"
                ):
                    continue
                if (
                    EXPECTED_ST_HEADER not in lines
                    or EXPECTED_MAN_HEADER not in lines
                ):
                    continue
                params = {
                    "callback": get_callback_address(
                        sock_addr[0], self.callback_port
                    ),
                    "date": format_date_time(time.time()),
                    "nls": self._nls_uuid,
                }
                try:
                    send_sock.sendto(
                        (SSDP_REPLY % params).encode("UTF-8"), sock_addr
                    )
                except OSError as err:
                    LOG.error(
                        "Failed to send SSDP reply to %r: %s", sock_addr, err
                    )

            if self._notify_enabled:
                self.send_notify("ssdp:byebye")
        except Exception as exp:
            self._thread_exception = exp  # Used in the stop() method.
            raise
        finally:
            recv_sock.close()
            send_sock.close()

    def start(self) -> None:
        """Start the server."""
        self._exit.clear()
        self._thread_exception = None
        self._thread = threading.Thread(
            target=self.respond_to_discovery,
            name="Wemo DiscoveryResponder Thread",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the server."""
        if self._thread:
            self._exit.set()
            self._thread.join()
            self._thread = None
            # Improve visibility of any exceptions that occurred on the thread.
            if self._thread_exception is not None:
                # pylint: disable=raising-bad-type
                raise self._thread_exception


if __name__ == "__main__":
    from pprint import pprint

    pprint("Scanning UPNP..")
    pprint(scan())
