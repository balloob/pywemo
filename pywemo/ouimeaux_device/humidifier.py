"""Representation of a WeMo Humidifier device."""

from __future__ import annotations

from enum import IntEnum
from typing import Any, TypedDict

from .api.attributes import AttributeDevice

_UNKNOWN = -1


# These enums were derived from Humidifier.deviceevent.GetAttributeList() and
# thus the names/values were not chosen randomly and the numbers have meaning.
class FanMode(IntEnum):
    """Enum to map WeMo FanModes to human-readable strings."""

    _UNKNOWN = _UNKNOWN
    # Note: The UpperMixedCase (invalid) names are deprecated.
    OFF = 0  # Fan and device turned off
    Off = 0  # pylint: disable=invalid-name
    MINIMUM = 1
    Minimum = 1  # pylint: disable=invalid-name
    LOW = 3
    Low = 2  # pylint: disable=invalid-name
    MEDIUM = 3
    Medium = 3  # pylint: disable=invalid-name
    HIGH = 4
    High = 4  # pylint: disable=invalid-name
    MAXIMUM = 5
    Maximum = 5  # pylint: disable=invalid-name

    @classmethod
    def _missing_(cls, value: Any) -> FanMode:
        return cls._UNKNOWN


FAN_MODE_NAMES = {
    FanMode.OFF: "Off",
    FanMode.MINIMUM: "Minimum",
    FanMode.LOW: "Low",
    FanMode.MEDIUM: "Medium",
    FanMode.HIGH: "High",
    FanMode.MAXIMUM: "Maximum",
}


class DesiredHumidity(IntEnum):
    """Enum to map WeMo DesiredHumidity to human-readable strings."""

    _UNKNOWN = _UNKNOWN
    # Note: The UpperMixedCase (invalid) names are deprecated.
    PERCENT_45 = 0
    FortyFivePercent = 0  # pylint: disable=invalid-name
    PERCENT_50 = 1
    FiftyPercent = 1  # pylint: disable=invalid-name
    PERCENT_55 = 2
    FiftyFivePercent = 2  # pylint: disable=invalid-name
    PERCENT_60 = 3
    SixtyPercent = 3  # pylint: disable=invalid-name
    PERCENT_100 = 4  # "Always On" Mode
    OneHundredPercent = 4  # pylint: disable=invalid-name

    @classmethod
    def _missing_(cls, value: Any) -> DesiredHumidity:
        return cls._UNKNOWN


DESIRED_HUMIDITY_NAMES = {
    DesiredHumidity.PERCENT_45: "45",
    DesiredHumidity.PERCENT_50: "50",
    DesiredHumidity.PERCENT_55: "55",
    DesiredHumidity.PERCENT_60: "60",
    DesiredHumidity.PERCENT_100: "100",
}


class WaterLevel(IntEnum):
    """Enum to map WeMo WaterLevel to human-readable strings."""

    # Note: The UpperMixedCase (invalid) names are deprecated.
    EMPTY = 0
    Empty = 0  # pylint: disable=invalid-name
    LOW = 1
    Low = 1  # pylint: disable=invalid-name
    GOOD = 2
    Good = 2  # pylint: disable=invalid-name


WATER_LEVEL_NAMES = {
    WaterLevel.EMPTY: "Empty",
    WaterLevel.LOW: "Low",
    WaterLevel.GOOD: "Good",
}

FILTER_LIFE_MAX = 60480


class _Attributes(TypedDict, total=False):
    FanMode: int
    DesiredHumidity: int
    CurrentHumidity: float
    NoWater: int
    WaterAdvise: int
    FilterLife: float
    ExpiredFilterTime: int


class Humidifier(AttributeDevice):
    """Representation of a WeMo Humidifier device."""

    _state_property = "fan_mode"  # Required by AttributeDevice.
    _attributes: _Attributes  # Required by AttributeDevice.

    @property
    def fan_mode(self) -> FanMode:
        """Return the FanMode setting (as an int index of the IntEnum)."""
        return FanMode(self._attributes.get("FanMode", _UNKNOWN))

    @property
    def fan_mode_string(self) -> str:
        """Return the FanMode setting as a string.

        (Off, Low, Medium, High, Maximum).
        """
        return FAN_MODE_NAMES.get(self.fan_mode, "Unknown")

    @property
    def desired_humidity(self) -> DesiredHumidity:
        """Return the desired humidity (as an int index of the IntEnum)."""
        return DesiredHumidity(
            self._attributes.get("DesiredHumidity", _UNKNOWN)
        )

    @property
    def desired_humidity_percent(self) -> str:
        """Return the desired humidity in percent (string)."""
        return DESIRED_HUMIDITY_NAMES.get(self.desired_humidity, "Unknown")

    @property
    def current_humidity_percent(self) -> float:
        """Return the observed relative humidity in percent (float)."""
        return self._attributes.get("CurrentHumidity", 0.0)

    @property
    def water_level(self) -> WaterLevel:
        """Return 0 if water level is Empty, 1 if Low, and 2 if Good."""
        if self._attributes.get("NoWater") == 1:
            return WaterLevel.EMPTY
        if self._attributes.get("WaterAdvise") == 1:
            return WaterLevel.LOW
        return WaterLevel.GOOD

    @property
    def water_level_string(self) -> str:
        """Return Empty, Low, or Good depending on the water level."""
        return WATER_LEVEL_NAMES.get(self.water_level, "Unknown")

    @property
    def filter_life_percent(self) -> float:
        """Return the percentage (float) of filter life remaining."""
        filter_life = self._attributes.get("FilterLife", 0.0)
        return round(filter_life / float(FILTER_LIFE_MAX) * 100.0, 2)

    @property
    def filter_expired(self) -> bool:
        """Return False if filter is OK, or True if it needs to be changed."""
        return bool(self._attributes.get("ExpiredFilterTime", 0))

    def get_state(self, force_update: bool = False) -> int:
        """Return 0 if off and 1 if on."""
        # The base implementation using GetBinaryState
        # doesn't work for Humidifier (always returns 0)
        # so use fan mode instead.
        # Consider the Humidifier to be "on" if it's not off.
        return int(super().get_state(force_update) != FanMode.Off)

    def set_state(self, state: int) -> None:
        """Set the fan mode of this device.

        Provided for compatibility with the Switch base class.

        Args:
          state: An int index of the FanMode IntEnum.
        """
        self.set_fan_mode(FanMode(state))

    def set_fan_mode(self, fan_mode: FanMode) -> None:
        """Set the fan mode of this device.

        Provided for compatibility with the Switch base class.
        """
        self.set_fan_mode_and_humidity(fan_mode=fan_mode)

    def set_humidity(self, desired_humidity: DesiredHumidity) -> None:
        """Set the desired humidity (as int index of the IntEnum)."""
        self.set_fan_mode_and_humidity(desired_humidity=desired_humidity)

    def set_fan_mode_and_humidity(
        self,
        fan_mode: FanMode | None = None,
        desired_humidity: DesiredHumidity | None = None,
    ) -> None:
        """Set the desired humidity and fan mode."""
        args: list[tuple[str, int]] = []

        if fan_mode is not None:
            if FanMode(fan_mode) == _UNKNOWN:
                raise ValueError(f"Unexpected value for fan_mode: {fan_mode}")
            args.append(("FanMode", fan_mode))

        if desired_humidity is not None:
            if DesiredHumidity(desired_humidity) == _UNKNOWN:
                raise ValueError(
                    "Unexpected value for desired_humidity: "
                    f"{desired_humidity}"
                )
            args.append(("DesiredHumidity", desired_humidity))

        self._set_attributes(*args)

    def reset_filter_life(self) -> None:
        """Reset the filter life (call this when you install a new filter)."""
        self._set_attributes(("FilterLife", FILTER_LIFE_MAX))
