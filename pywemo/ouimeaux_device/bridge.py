"""Representation of a WeMo Bridge (Link) device."""

from __future__ import annotations

import io
import time
import warnings
from html import escape
from typing import Any, Iterable, TypedDict

from lxml import etree as et

from ..color import ColorXY, get_profiles, limit_to_gamut
from ..exceptions import InvalidSchemaError
from . import Device
from .api.service import RequiredService

CAPABILITY_ID2NAME = dict(
    (
        ("10006", "onoff"),
        ("10008", "levelcontrol"),
        ("30008", "sleepfader"),
        ("30009", "levelcontrol_move"),
        ("3000A", "levelcontrol_stop"),
        ("10300", "colorcontrol"),
        ("30301", "colortemperature"),
    )
)
CAPABILITY_NAME2ID = dict(
    (val, cap) for cap, val in CAPABILITY_ID2NAME.items()
)

# acceptable values for 'onoff'
OFF = 0
ON = 1
TOGGLE = 2


def _warn_rename(old: str, new: str, stacklevel: int = 3) -> None:
    warnings.warn(
        f"{old} is deprecated and will be removed in a future release. "
        f"Use {new} instead",
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def limit(value: int, min_val: int, max_val: int) -> int:
    """Return a value clipped to the range [min_val, max_val]."""
    return max(min_val, min(value, max_val))


class Bridge(Device):
    """Representation of a WeMo Bridge (Link) device."""

    lights: dict[str, Light]
    groups: dict[str, Group]

    EVENT_TYPE_STATUS_CHANGE = "StatusChange"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create a WeMo Bridge (Link) device."""
        super().__init__(*args, **kwargs)
        self.lights = {}
        self.groups = {}
        self.bridge_update()

    def __repr__(self) -> str:
        """Return a string representation of the device."""
        return (
            '<WeMo Bridge "{name}", Lights: {lights}, ' + "Groups: {groups}>"
        ).format(
            name=self.name, lights=len(self.lights), groups=len(self.groups)
        )

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(name="basicevent", actions=["GetMacAddr"]),
            RequiredService(
                name="bridge",
                actions=["GetDeviceStatus", "SetDeviceStatus"],
            ),
        ]

    @property
    def Lights(self) -> dict[str, Light]:  # pylint: disable=invalid-name
        """Deprecated method for accessing .lights."""
        _warn_rename("Lights", "lights")
        return self.lights

    @property
    def Groups(self) -> dict[str, Group]:  # pylint: disable=invalid-name
        """Deprecated method for accessing .groups."""
        _warn_rename("Groups", "groups")
        return self.groups

    def bridge_update(
        self, force_update: bool = True
    ) -> tuple[dict[str, Light], dict[str, Group]]:
        """Get updated status information for the bridge and its lights."""
        if force_update or self.lights is None or self.groups is None:
            plugin_udn = self.basicevent.GetMacAddr().get("PluginUDN")

            if hasattr(self.bridge, "GetEndDevicesWithStatus"):
                end_devices = self.bridge.GetEndDevicesWithStatus(
                    DevUDN=plugin_udn, ReqListType="PAIRED_LIST"
                )
            else:
                end_devices = self.bridge.GetEndDevices(
                    DevUDN=plugin_udn, ReqListType="PAIRED_LIST"
                )

            if not (end_devices_xml := end_devices.get("DeviceLists")):
                return self.lights, self.groups

            end_device_list = et.fromstring(
                end_devices_xml.encode("utf-8"),
                parser=et.XMLParser(resolve_entities=False),
            )

            for light in end_device_list.iter("DeviceInfo"):
                if not (device_id := light.findtext("DeviceID")):
                    raise InvalidSchemaError(
                        f"DeviceID missing: {et.tostring(light).decode()}"
                    )
                if device_id in self.lights:
                    self.lights[device_id].update_state(light)
                else:
                    self.lights[device_id] = Light(self, light)

            for group in end_device_list.iter("GroupInfo"):
                if not (group_id := group.findtext("GroupID")):
                    raise InvalidSchemaError(
                        f"GroupID missing: {et.tostring(group).decode()}"
                    )
                if group_id in self.groups:
                    self.groups[group_id].update_state(group)
                else:
                    self.groups[group_id] = Group(self, group)

        return self.lights, self.groups

    def get_state(self, force_update: bool = False) -> int:
        """Update the state of the Bridge device."""
        state = super().get_state(force_update)
        self.bridge_update(force_update)
        return state

    def subscription_update(self, _type: str, _param: str) -> bool:
        """Update the bridge attributes due to a subscription update event."""
        if _type == self.EVENT_TYPE_STATUS_CHANGE and _param:
            try:
                state_event = et.fromstring(
                    _param.encode("utf8"),
                    parser=et.XMLParser(resolve_entities=False),
                )
            except et.XMLSyntaxError:
                return False
            if not (key := state_event.findtext("DeviceID")):
                return False
            if key in self.lights:
                return self.lights[key].subscription_update(state_event)

            if key in self.groups:
                return self.groups[key].subscription_update(state_event)

            return False
        return super().subscription_update(_type, _param)

    def bridge_getdevicestatus(self, deviceid: str) -> et._Element | None:
        """Return the list of device statuses for the bridge's lights."""
        status_list = self.bridge.GetDeviceStatus(DeviceIDs=deviceid)
        device_status_list_xml = status_list.get("DeviceStatusList")
        if not device_status_list_xml:
            return None
        device_status_list = et.fromstring(
            device_status_list_xml.encode("utf-8"),
            parser=et.XMLParser(resolve_entities=False),
        )

        return device_status_list.find("DeviceStatus")

    def bridge_setdevicestatus(
        self, isgroup: str, deviceid: str, capids: list[str], values: list[str]
    ) -> dict[str, str]:
        """Set the status of the bridge's lights."""
        req = et.Element("DeviceStatus")
        et.SubElement(req, "IsGroupAction").text = isgroup
        et.SubElement(req, "DeviceID", available="YES").text = deviceid
        et.SubElement(req, "CapabilityID").text = ",".join(capids)
        et.SubElement(req, "CapabilityValue").text = ",".join(values)

        buf = io.BytesIO()
        et.ElementTree(req).write(buf, encoding="UTF-8", xml_declaration=True)
        send_state = escape(buf.getvalue().decode(), quote=True)

        return self.bridge.SetDeviceStatus(DeviceStatusList=send_state)


class DeviceState(TypedDict, total=False):
    """LinkedDevice state dictionary type."""

    available: bool
    onoff: int
    level: int
    temperature_mireds: int
    temperature_kelvin: int
    color_xy: ColorXY


class LinkedDevice:  # pylint: disable=too-many-instance-attributes
    """Representation of a device connected to the bridge."""

    _NAME_TAG: str
    _CAPABILITIES_TAGS: tuple[str, ...]
    _VALUES_TAGS: tuple[str, ...]

    def __init__(self, bridge: Bridge, info: et._Element) -> None:
        """Create a Linked Device."""
        self.bridge: Bridge = bridge
        self.host: str = self.bridge.host
        self.port: int = self.bridge.port
        self.name: str = ""
        self.state: DeviceState = {}
        self.capabilities: Iterable[str] = tuple()
        self.update_state(info)
        self._last_err: dict[str, str] = {}
        self.mac: str = self.bridge.mac
        self.serial_number: str = self.bridge.serial_number
        self.uniqueID: str = ""  # pylint: disable=invalid-name

    def get_state(self, force_update: bool = False) -> DeviceState:
        """Return the status of the device."""
        if force_update:
            self.bridge.bridge_update()
        return self.state

    def update_state(self, status: et._Element) -> None:
        """Fetch the capabilities and values then update the device state."""
        if name := status.findtext(self._NAME_TAG, ""):
            self.name = name

        def get_first_text(tags: Iterable[str]) -> str | None:
            candidates = (status.findtext(tag) for tag in tags)
            return next(filter(bool, candidates), None)

        if capabilities := get_first_text(self._CAPABILITIES_TAGS):
            self.capabilities = tuple(
                CAPABILITY_ID2NAME.get(c, c) for c in capabilities.split(",")
            )
            if current_state := get_first_text(self._VALUES_TAGS):
                self._update_values(
                    zip(self.capabilities, current_state.split(","))
                )

    def _update_values(self, values: Iterable[tuple[str, str]]) -> None:
        """Set the device state based on capabilities and values."""
        status: dict[str, tuple[int, ...] | None] = {}
        for capability, value in values:
            if capability not in CAPABILITY_NAME2ID:
                continue  # Ignore unsupported capabilities.
            if not value:
                status[capability] = None
                continue
            try:
                status[capability] = tuple(
                    int(round(float(v))) for v in value.split(":")
                )
            except ValueError as err:
                raise ValueError(
                    f"Invalid value for {capability}: {repr(value)}"
                ) from err

        # unreachable devices have empty strings for all capability values
        if (on_off := status.get("onoff", ("Missing",))) is None:
            self.state["available"] = False
            self.state["onoff"] = 0
        elif isinstance(on_off[0], int):
            self.state["available"] = True
            self.state["onoff"] = on_off[0]

        if (level_control := status.get("levelcontrol")) is not None:
            self.state["level"] = level_control[0]

        if (color_temperature := status.get("colortemperature")) is not None:
            temperature = color_temperature[0]
            if temperature <= 0:
                raise ValueError(
                    f"Invalid value for color temperature: {temperature}"
                )
            self.state["temperature_mireds"] = temperature
            self.state["temperature_kelvin"] = int(1000000 / temperature)

        if (color_control := status.get("colorcontrol")) is not None:
            if len(color_control) < 2:
                raise ValueError(
                    f"Too few values for colorcontrol: {repr(color_control)}"
                )
            color_x, color_y = float(color_control[0]), float(color_control[1])
            color_x, color_y = color_x / 65535.0, color_y / 65535.0
            self.state["color_xy"] = color_x, color_y

    def subscription_update(self, state_event: et._Element) -> bool:
        """Update the light values due to a subscription update event."""
        if (
            device_id := state_event.find("DeviceID")
        ) is None or device_id.get("available", "YES").upper() == "YES":
            capability = state_event.findtext("CapabilityId")
            value = state_event.findtext("Value")
        else:
            capability = CAPABILITY_NAME2ID.get("onoff")
            value = ""  # Use an empty string to indicate an unreachable device

        if capability is None or value is None:
            return False

        name = CAPABILITY_ID2NAME.get(capability, capability)
        if name not in self.capabilities:
            # Should't receive updates for capabilities that were not
            # originally present.
            return False

        try:
            self._update_values([(name, value)])
        except ValueError:
            return False
        return True

    def _setdevicestatus(self, **kwargs: Any) -> LinkedDevice:
        """Ask the bridge to set the device status."""
        isgroup = "YES" if isinstance(self, Group) else "NO"

        capids = []
        values = []
        for cap, val in kwargs.items():
            capids.append(CAPABILITY_NAME2ID[cap])

            if not isinstance(val, (list, tuple)):
                val = (val,)
            values.append(":".join(str(v) for v in val))

        self._last_err = self.bridge.bridge_setdevicestatus(
            isgroup, self.uniqueID, capids, values
        )
        return self

    def turn_on(  # pylint: disable=unused-argument
        self,
        level: int | None = None,
        transition: int = 0,
        force_update: bool = False,
    ) -> LinkedDevice:
        """Turn on the device."""
        return self._setdevicestatus(onoff=ON)

    def turn_off(  # pylint: disable=unused-argument
        self, transition: int = 0
    ) -> LinkedDevice:
        """Turn off the device."""
        return self._setdevicestatus(onoff=OFF)

    def toggle(self) -> LinkedDevice:
        """Toggle the device from on to off or off to on."""
        return self._setdevicestatus(onoff=TOGGLE)

    @property
    def device_type(self) -> str:
        """Return what kind of WeMo this device is."""
        return type(self).__name__

    def __repr__(self) -> str:
        """Return a string representation of the device."""
        return f'<{self.device_type.upper()} "{self.name}">'


class Light(LinkedDevice):  # pylint: disable=too-many-instance-attributes
    """Representation of a Light connected to the Bridge."""

    _NAME_TAG = "FriendlyName"
    _CAPABILITIES_TAGS = ("CapabilityIDs", "CapabilityID")
    _VALUES_TAGS = ("CurrentState", "CapabilityValue")

    def __init__(self, bridge: Bridge, info: et._Element) -> None:
        """Create a Light device."""
        super().__init__(bridge, info)

        self.device_index: str = info.findtext("DeviceIndex", "")
        self.uniqueID: str = info.findtext("DeviceID", "")
        self.iconvalue: str = info.findtext("IconVersion", "")
        self.firmware: str = info.findtext("FirmwareVersion", "")
        self.manufacturer: str = info.findtext("Manufacturer", "")
        self.model: str = info.findtext("ModelCode", "")
        self.certified: str = info.findtext("WeMoCertified", "")

        self.temperature_range, self.gamut = get_profiles(self.model)
        self._pending: dict[str, Any] = {}

    def _queuedevicestatus(self, queue: bool = False, **kwargs: Any) -> Light:
        """Queue an update to the device."""
        if kwargs:
            self._pending.update(kwargs)
        if not queue:
            self._setdevicestatus(**self._pending)
            self._pending = {}

        return self

    def turn_on(
        self,
        level: int | None = None,
        transition: int = 0,
        force_update: bool = False,
    ) -> Light:
        """Turn on the light."""
        transition_time = limit(int(transition * 10), 0, 65535)

        if level == 0:
            return self.turn_off(transition)
        if "levelcontrol" in self.capabilities:
            # Work around observed fw bugs.
            # - When we set a new brightness level but the bulb is off, it
            #   first turns on at the old brightness and then fades to the new
            #   setting. So we have to force the saved brightness to 0 first.
            # - When we turn a bulb on with levelcontrol the onoff state
            #   doesn't update.
            # - After turning off a bulb with sleepfader, it fails to turn back
            #   on unless the brightness is re-set with levelcontrol.
            self.get_state(force_update=force_update)
            # A freshly power cycled bridge has no record of the bulb
            # brightness, so default to full on if the client didn't request
            # a level and we have no record
            if level is None:
                level = self.state.get("level", 255)

            if self.state["onoff"] == 0:
                self._setdevicestatus(levelcontrol=(0, 0), onoff=ON)

            level = limit(int(level), 0, 255)
            return self._queuedevicestatus(
                levelcontrol=(level, transition_time)
            )

        return self._queuedevicestatus(onoff=ON)

    def turn_off(self, transition: int = 0) -> Light:
        """Turn off the light."""
        if transition and "sleepfader" in self.capabilities:
            # Sleepfader control did not turn off bulb when fadetime was 0
            transition_time = limit(int(transition * 10), 1, 65535)
            reference = int(time.time())
            return self._queuedevicestatus(
                sleepfader=(transition_time, reference)
            )

        return self._queuedevicestatus(onoff=OFF)

    def set_temperature(
        self,
        kelvin: int = 2700,
        mireds: int | None = None,
        transition: int = 0,
        delay: bool = True,
    ) -> Light:
        """Set the color temperature of the light."""
        transition_time = limit(int(transition * 10), 0, 65535)
        if mireds is None:
            mireds = int(1000000 / kelvin)
        mireds = limit(int(mireds), *self.temperature_range)
        return self._queuedevicestatus(
            colortemperature=(mireds, transition_time), queue=delay
        )

    def set_color(
        self, colorxy: ColorXY, transition: int = 0, delay: bool = True
    ) -> Light:
        """Set the color of the light."""
        transition_time = limit(int(transition * 10), 0, 65535)
        colorxy = limit_to_gamut(colorxy, self.gamut)
        colorx = limit(int(colorxy[0] * 65535), 0, 65535)
        colory = limit(int(colorxy[1] * 65535), 0, 65535)
        return self._queuedevicestatus(
            colorcontrol=(colorx, colory, transition_time), queue=delay
        )

    def start_ramp(self, ramp_up: bool, rate: int) -> Light:
        """Start ramping the brightness up or down."""
        up_down = "1" if ramp_up else "0"
        rate = limit(int(rate), 0, 255)
        return self._queuedevicestatus(levelcontrol_move=(up_down, rate))

    def stop_ramp(self) -> LinkedDevice:
        """Start ramping the brightness up or down."""
        return self._setdevicestatus(levelcontrol_stop="")


class Group(LinkedDevice):
    """Representation of a Group of lights connected to the Bridge."""

    _NAME_TAG = "GroupName"
    _CAPABILITIES_TAGS = ("GroupCapabilityIDs", "CapabilityID")
    _VALUES_TAGS = ("GroupCapabilityValues", "CapabilityValue")

    def __init__(self, bridge: Bridge, info: et._Element) -> None:
        """Create a Group device."""
        super().__init__(bridge, info)
        self.uniqueID: str = info.findtext("GroupID", "")
