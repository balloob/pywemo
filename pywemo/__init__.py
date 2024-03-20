r"""Lightweight Python module to discover and control WeMo devices.
.. include:: README.md
"""

# flake8: noqa F401

from .discovery import (
    device_from_description,
    discover_devices,
    setup_url_for_address,
)
from .exceptions import PyWeMoException
from .ouimeaux_device import Device as WeMoDevice
from .ouimeaux_device.api.long_press import LongPressMixin
from .ouimeaux_device.api.service import Action, Service
from .ouimeaux_device.bridge import Bridge
from .ouimeaux_device.bridge import Group as BridgeGroup
from .ouimeaux_device.bridge import Light as BridgeLight
from .ouimeaux_device.coffeemaker import CoffeeMaker, CoffeeMakerMode
from .ouimeaux_device.crockpot import CrockPot, CrockPotMode
from .ouimeaux_device.dimmer import Dimmer, DimmerLongPress, DimmerV2
from .ouimeaux_device.humidifier import (
    DesiredHumidity,
    FanMode,
    Humidifier,
    WaterLevel,
)
from .ouimeaux_device.insight import Insight, StandbyState
from .ouimeaux_device.lightswitch import LightSwitch, LightSwitchLongPress
from .ouimeaux_device.maker import Maker
from .ouimeaux_device.motion import Motion
from .ouimeaux_device.outdoor_plug import OutdoorPlug
from .ouimeaux_device.switch import Switch
from .subscribe import SubscriptionRegistry
