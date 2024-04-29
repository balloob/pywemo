"""Representation of a WeMo CrockPot device."""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any, TypedDict

from .api.service import RequiredService
from .switch import Switch

_UNKNOWN = -1

LOG = logging.getLogger(__name__)


# These enums were derived from the CrockPot.basicevent.GetCrockpotState()
# service call. Thus these names/values were not chosen randomly and the
# numbers have meaning.
class CrockPotMode(IntEnum):
    """Modes for the CrockPot."""

    _UNKNOWN = _UNKNOWN
    # Note: The UpperMixedCase (invalid) names are deprecated.
    OFF = 0
    Off = 0  # pylint: disable=invalid-name
    WARM = 50
    Warm = 50  # pylint: disable=invalid-name
    LOW = 51
    Low = 51  # pylint: disable=invalid-name
    HIGH = 52
    High = 52  # pylint: disable=invalid-name

    @classmethod
    def _missing_(cls, value: Any) -> CrockPotMode:
        return cls._UNKNOWN


MODE_NAMES = {
    CrockPotMode.OFF: "Turned Off",
    CrockPotMode.WARM: "Warm",
    CrockPotMode.LOW: "Low",
    CrockPotMode.HIGH: "High",
}


class _Attributes(TypedDict, total=False):
    """CrockPot state dictionary type."""

    cookedTime: int
    mode: int
    time: int


class CrockPot(Switch):
    """WeMo Crockpot."""

    EVENT_TYPE_COOKED_TIME = "cookedTime"
    EVENT_TYPE_MODE = "mode"
    EVENT_TYPE_TIME = "time"

    _attributes: _Attributes

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create a WeMo CrockPot device."""
        super().__init__(*args, **kwargs)
        self._attributes = {}
        self.get_state(True)

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(
                name="basicevent",
                actions=["GetCrockpotState", "SetCrockpotState"],
            ),
        ]

    def update_attributes(self) -> None:
        """Request state from device."""
        state_attributes = self.basicevent.GetCrockpotState()

        # Only update our state on complete updates from the device
        try:
            self._attributes = {
                "cookedTime": int(state_attributes["cookedTime"]),
                "mode": int(state_attributes["mode"]),
                "time": int(state_attributes["time"]),
            }
        except KeyError as err:
            LOG.error("Missing expected state attribute: %r", err)
        except ValueError as err:
            LOG.error("Invalid state value: %r", err)
        else:
            self._state = self.mode

    def subscription_update(self, _type: str, _params: str) -> bool:
        """Handle reports from device."""
        try:
            if _type == self.EVENT_TYPE_MODE:
                self._attributes["mode"] = int(_params)
                self._state = self.mode
                return True
            if _type == self.EVENT_TYPE_TIME:
                self._attributes["time"] = int(_params)
                return True
            if _type == self.EVENT_TYPE_COOKED_TIME:
                self._attributes["cookedTime"] = int(_params)
                return True
        except ValueError as err:
            LOG.error("Invalid value for %s: %r", _type, err)
        return super().subscription_update(_type, _params)

    @property
    def mode(self) -> CrockPotMode:
        """Return the mode of the device."""
        return CrockPotMode(self._attributes.get("mode", _UNKNOWN))

    @property
    def mode_string(self) -> str:
        """Return the mode of the device as a string."""
        return MODE_NAMES.get(self.mode, "Unknown")

    @property
    def remaining_time(self) -> int:
        """Return the remaining time in minutes."""
        return self._attributes.get("time", 0)

    @property
    def cooked_time(self) -> int:
        """Return the cooked time in minutes."""
        return self._attributes.get("cookedTime", 0)

    def get_state(self, force_update: bool = False) -> int:
        """Return 0 if off and 1 if on."""
        # The base implementation using GetBinaryState doesn't work for
        # CrockPot (always returns 0) so use mode instead.
        if force_update or self._attributes.get("mode") is None:
            self.update_attributes()

        return int(self.mode != CrockPotMode.OFF)

    def set_state(self, state: int) -> None:
        """Set the state of this device to on or off."""
        if state:
            self.update_settings(CrockPotMode.HIGH, self.remaining_time)
        else:
            self.update_settings(CrockPotMode.OFF, 0)

    def update_settings(self, mode: CrockPotMode, time: int) -> None:
        """Update mode and cooking time."""
        if CrockPotMode(mode) == _UNKNOWN:
            raise ValueError(f"Unknown CrockPotMode: {mode}")
        self.basicevent.SetCrockpotState(mode=str(int(mode)), time=str(time))

        # The CrockPot might not be ready - so it's not safe to assume the
        # state is what you just set so re-read it from the device.
        self.get_state(True)
