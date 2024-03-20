"""Representation of a WeMo CoffeeMaker device."""

from __future__ import annotations

from enum import IntEnum
from typing import Any, TypedDict

from .api.attributes import AttributeDevice

_UNKNOWN = -1


# These enums were derived from the
# CoffeeMaker.deviceevent.GetAttributeList() service call
# Thus these names/values were not chosen randomly
# and the numbers have meaning.
class CoffeeMakerMode(IntEnum):
    """Enum to map WeMo modes to human-readable strings."""

    _UNKNOWN = _UNKNOWN
    # Note: The UpperMixedCase (invalid) names are deprecated.
    REFILL = 0  # reservoir empty and carafe not in place
    Refill = 0  # pylint: disable=invalid-name
    PLACE_CARAFE = 1  # reservoir has water but carafe not present
    PlaceCarafe = 1  # pylint: disable=invalid-name
    REFILL_WATER = 2  # carafe present but reservoir is empty
    RefillWater = 2  # pylint: disable=invalid-name
    READY = 3
    Ready = 3  # pylint: disable=invalid-name
    BREWING = 4
    Brewing = 4  # pylint: disable=invalid-name
    BREWED = 5
    Brewed = 5  # pylint: disable=invalid-name
    CLEANING_BREWING = 6
    CleaningBrewing = 6  # pylint: disable=invalid-name
    CLEANING_SOAKING = 7
    CleaningSoaking = 7  # pylint: disable=invalid-name
    BREW_FAILED_CARAFE_REMOVED = 8
    BrewFailCarafeRemoved = 8  # pylint: disable=invalid-name

    @classmethod
    def _missing_(cls, value: Any) -> CoffeeMakerMode:
        return cls._UNKNOWN


MODE_NAMES = {
    CoffeeMakerMode.REFILL: "Refill",
    CoffeeMakerMode.PLACE_CARAFE: "PlaceCarafe",
    CoffeeMakerMode.REFILL_WATER: "RefillWater",
    CoffeeMakerMode.READY: "Ready",
    CoffeeMakerMode.BREWING: "Brewing",
    CoffeeMakerMode.BREWED: "Brewed",
    CoffeeMakerMode.CLEANING_BREWING: "CleaningBrewing",
    CoffeeMakerMode.CLEANING_SOAKING: "CleaningSoaking",
    CoffeeMakerMode.BREW_FAILED_CARAFE_REMOVED: "BrewFailCarafeRemoved",
}


class _Attributes(TypedDict, total=False):
    Mode: int


class CoffeeMaker(AttributeDevice):
    """Representation of a WeMo CoffeeMaker device."""

    _state_property = "mode"  # Required by AttributeDevice.
    _attributes: _Attributes  # Required by AttributeDevice.

    @property
    def mode(self) -> CoffeeMakerMode:
        """Return the mode of the device."""
        return CoffeeMakerMode(self._attributes.get("Mode", _UNKNOWN))

    @property
    def mode_string(self) -> str:
        """Return the mode of the device as a string."""
        return MODE_NAMES.get(self.mode, "Unknown")

    def get_state(self, force_update: bool = False) -> int:
        """Return 0 if off and 1 if on."""
        # The base implementation using GetBinaryState doesn't work for
        # CoffeeMaker (always returns 0), so use mode instead.
        # Consider the Coffee Maker to be "on" if it's currently brewing.
        return int(super().get_state(force_update) == CoffeeMakerMode.BREWING)

    def set_state(self, state: int) -> None:
        """Set the state of this device to on or off."""
        # CoffeeMaker cannot be turned off remotely, so ignore the request if
        # state is "falsey"
        if state:
            # Coffee Maker always responds with an error if SetBinaryState is
            # called. Use SetAttributes to change the Mode to "Brewing"
            self._set_attributes(("Mode", CoffeeMakerMode.BREWING))
