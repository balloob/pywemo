"""Representation of a WeMo Motion device."""

from .api.long_press import LongPressMixin
from .switch import Switch


class LightSwitch(Switch):
    """Representation of a WeMo Light Switch device."""


class LightSwitchLongPress(LightSwitch, LongPressMixin):
    """WeMo Light Switch that supports long press notifications."""
