"""Module to discover WeMo devices."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from socket import gaierror, gethostbyname
from typing import Any, Callable

import requests

from . import ssdp
from .exceptions import (
    InvalidSchemaError,
    MissingServiceError,
    PyWeMoException,
)
from .ouimeaux_device import Device, UnsupportedDevice, probe_wemo
from .ouimeaux_device.api.service import REQUESTS_TIMEOUT
from .ouimeaux_device.api.xsd_types import DeviceDescription
from .ouimeaux_device.bridge import Bridge
from .ouimeaux_device.coffeemaker import CoffeeMaker
from .ouimeaux_device.crockpot import CrockPot
from .ouimeaux_device.dimmer import Dimmer, DimmerLongPress, DimmerV2
from .ouimeaux_device.humidifier import Humidifier
from .ouimeaux_device.insight import Insight
from .ouimeaux_device.lightswitch import LightSwitch, LightSwitchLongPress
from .ouimeaux_device.maker import Maker
from .ouimeaux_device.motion import Motion
from .ouimeaux_device.outdoor_plug import OutdoorPlug
from .ouimeaux_device.switch import Switch

LOG = logging.getLogger(__name__)
_uuid_seen = set()  # See _call_once_per_uuid.


def _call_once_per_uuid(
    uuid: str, method: Callable[..., Any], *args: Any, **kwargs: Any
) -> None:
    key = (uuid, method)
    if key in _uuid_seen:
        return
    _uuid_seen.add(key)
    method(*args, *kwargs)


def discover_devices(*, debug: bool = False, **kwargs: Any) -> list[Device]:
    """Find WeMo devices on the local network."""
    devices = (
        device_from_uuid_and_location(entry.udn, entry.location, debug=debug)
        for entry in ssdp.scan(**kwargs)
    )
    return [d for d in devices if d is not None]


def device_from_description(
    description_url: str, *, debug: bool = False
) -> Device | None:
    """Return object representing WeMo device running at host, else None."""
    try:
        xml = requests.get(description_url, timeout=REQUESTS_TIMEOUT)
    except requests.RequestException:
        LOG.exception("Failed to fetch description %s", description_url)
        return None

    try:
        device = DeviceDescription.from_xml(xml.content)
    except PyWeMoException:
        LOG.exception("Failed to parse description %s", description_url)
        return None

    return device_from_uuid_and_location(
        device.udn, description_url, debug=debug
    )


def device_from_uuid_and_location(  # noqa: C901
    # pylint: disable=too-many-branches,too-many-return-statements
    uuid: str | None,
    location: str | None,
    *,
    debug: bool = False,
) -> Device | None:
    """Determine device class based on the device uuid."""
    if not (uuid and location):
        return None
    try:
        if uuid.startswith("uuid:Socket"):
            return Switch(location)
        if uuid.startswith("uuid:Lightswitch-1_0"):
            if not location.endswith("/setup.xml"):
                return LightSwitch(location)
            return LightSwitchLongPress(location)
        if uuid.startswith("uuid:Lightswitch-2_0"):
            return LightSwitchLongPress(location)
        if uuid.startswith("uuid:Lightswitch-3_0"):
            return LightSwitchLongPress(location)
        if uuid.startswith("uuid:Lightswitch"):
            return LightSwitch(location)
        if uuid.startswith("uuid:Dimmer-1_0"):
            return DimmerLongPress(location)
        if uuid.startswith("uuid:Dimmer-2_0"):
            return DimmerV2(location)
        if uuid.startswith("uuid:Dimmer"):
            return Dimmer(location)
        if uuid.startswith("uuid:Insight"):
            return Insight(location)
        if uuid.startswith("uuid:Sensor"):
            return Motion(location)
        if uuid.startswith("uuid:Maker"):
            return Maker(location)
        if uuid.startswith("uuid:Bridge"):
            return Bridge(location)
        if uuid.startswith("uuid:CoffeeMaker"):
            return CoffeeMaker(location)
        if uuid.startswith("uuid:Crockpot"):
            return CrockPot(location)
        if uuid.startswith("uuid:Humidifier"):
            return Humidifier(location)
        if uuid.startswith("uuid:OutdoorPlug"):
            return OutdoorPlug(location)
    except (InvalidSchemaError, MissingServiceError) as err:
        _call_once_per_uuid(
            uuid,
            LOG.info,
            "pyWeMo encountered a non-WeMo device %s %s: %r",
            uuid,
            location,
            err,
        )
        # Fall-through: Try UnsupportedDevice if debug is enabled.
    except PyWeMoException:
        _call_once_per_uuid(
            uuid, LOG.exception, "Device setup failed %s %s", uuid, location
        )
        # Fall-through: Try UnsupportedDevice if debug is enabled.

    if uuid.startswith("uuid:") and debug:
        # unsupported device, but if this function was called from
        # discover_devices then this should be a Belkin product and is probably
        # a WeMo product without a custom class yet.  So attempt to return a
        # basic object to allow manual interaction.
        try:
            device = UnsupportedDevice(location)
        except PyWeMoException:
            LOG.exception("Device setup failed %s %s", uuid, location)
        else:
            LOG.info(
                "Device with %s is not supported by pywemo, returning "
                "UnsupportedDevice object to allow manual interaction",
                uuid,
            )
            return device

    return None


def hostname_lookup(hostname: str) -> str:
    """Resolve a hostname into an IP address."""
    try:
        # The {host} must be resolved to an IP address; if this fails, this
        # will throw a socket.gaierror.
        host_address = gethostbyname(hostname)

        # Reset {host} to the resolved address.
        LOG.debug(
            "Resolved hostname %s to IP address %s.", hostname, host_address
        )
        return host_address

    except gaierror:
        # The {host}-as-hostname did not resolve to an IP address.
        LOG.debug("Could not resolve hostname %s to an IP address.", hostname)
        return hostname


def setup_url_for_address(host: str, port: int | None = None) -> str | None:
    """Determine setup.xml url for a given host and port pair."""
    # Force hostnames into IP addresses
    try:
        # Attempt to register {host} as an IP address; if this fails ({host} is
        # not an IP address), this will throw a ValueError.
        ip_address(host)
    except ValueError:
        # The provided {host} should be treated as a hostname.
        host = hostname_lookup(host)

    # Automatically determine the port if not provided.
    if not port:
        port = probe_wemo(host)

    if not port:
        return None

    return f"http://{host}:{port}/setup.xml"
