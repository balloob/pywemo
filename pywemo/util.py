"""Miscellaneous utility functions."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta

import ifaddr


def interface_addresses() -> list[str]:
    """
    Return local address for broadcast/multicast.

    Return local address of any network associated with a local interface
    that has broadcast (and probably multicast) capability.
    """
    addresses = []

    for iface in ifaddr.get_adapters():
        for addr in iface.ips:
            if not (addr.is_IPv4 and isinstance(addr.ip, str)):
                continue
            if addr.ip == "127.0.0.1":
                continue

            addresses.append(addr.ip)

    return addresses


def get_callback_address(host: str, port: int) -> str | None:
    """Return IP address & port used by devices to send event notifications."""
    pywemo_callback_address = os.getenv("PYWEMO_CALLBACK_ADDRESS")
    if pywemo_callback_address is not None:
        return pywemo_callback_address

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, 9))
        return f"{sock.getsockname()[0]}:{port}"
    except OSError:
        return None
    finally:
        del sock


def signal_strength_to_dbm(value: dict[str, str] | str) -> int:
    """Convert signal strength percentage into a RSSI dBm value.

    WeMo devices use the algorithm described here to convert a RSSI dBm value
    into a signal strength percentage:
    https://community.cambiumnetworks.com/t/cnmaestro-wifi-analyzer-tool/63471

    signal_strength_to_dbm is meant to be used with the
    basicevent.GetSignalStrength UPnP Action.

    signal_strength = device.basicevent.GetSignalStrength()
    signal_strength_to_dbm(signal_strength)
      or
    signal_strength_to_dbm(signal_strength["SignalStrength"])
    """
    if isinstance(value, dict):
        percent_str = value["SignalStrength"]
    else:
        percent_str = value

    percent = round(float(percent_str))
    if percent >= 100:
        return -50
    if percent >= 24:
        return round((percent - 24) * 10 / 26 - 80)
    if percent > 0:
        return round(percent * 10 / 26 - 90)
    return -90


@dataclass
class MetaInfo:
    """Parsed output of the metainfo.GetMetaInfo() Action."""

    mac: str
    serial_number: str
    device_sku: str
    firmware_version: str
    access_point_ssid: str
    model_name: str

    @classmethod
    def from_meta_info(cls, value: dict[str, str] | str) -> MetaInfo:
        """Initialize from metainfo.GetMetaInfo() output."""
        info_str = value["MetaInfo"] if isinstance(value, dict) else value
        if not info_str.isprintable():
            raise ValueError("Invalid characters found in MetaInfo")
        values = info_str.split("|")
        if len(values) < 6:
            raise ValueError(f"Could not unpack MetaInfo: {info_str}")
        return cls(*values[:6])


@dataclass
class ExtMetaInfo:  # pylint: disable=too-many-instance-attributes
    """Parsed output of the metainfo.GetExtMetaInfo() Action."""

    current_client_state: int
    ice_running: int
    nat_initialized: int
    last_auth_value: int
    uptime: timedelta
    firmware_update_state: int
    utc_time: datetime
    home_id: str
    remote_access_enabled: bool
    model_name: str

    @classmethod
    def from_ext_meta_info(cls, value: dict[str, str] | str) -> ExtMetaInfo:
        """Initialize from metainfo.GetExtMetaInfo() output."""
        info_str = value["ExtMetaInfo"] if isinstance(value, dict) else value
        if not info_str.isprintable():
            raise ValueError("Invalid characters found in ExtMetaInfo")
        values = info_str.split("|")
        if not len(values) > 9:
            raise ValueError(f"Could not unpack ExtMetaInfo: {info_str}")

        hours, minutes, seconds = (int(v) for v in values[4].split(":"))
        return cls(
            current_client_state=int(values[0]),
            ice_running=int(values[1]),
            nat_initialized=int(values[2]),
            last_auth_value=int(values[3]),
            uptime=timedelta(hours=hours, minutes=minutes, seconds=seconds),
            firmware_update_state=int(values[5]),
            utc_time=datetime.utcfromtimestamp(int(values[6])),
            home_id=values[7],
            remote_access_enabled=bool(int(values[8])),
            model_name=values[9],
        )
