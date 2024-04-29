"""Integration tests for the Dimmer class."""

import threading

import pytest

from pywemo.discovery import device_from_uuid_and_location
from pywemo.subscribe import EVENT_TYPE_BINARY_STATE

from .api.unit import long_press_helpers


class Base:
    """Tests that run for each Dimmer model."""

    @pytest.mark.vcr()
    def test_turn_on(self, dimmer):
        dimmer.on()
        assert dimmer.get_state(force_update=True) == 1

    @pytest.mark.vcr()
    def test_turn_off(self, dimmer):
        dimmer.off()
        assert dimmer.get_state(force_update=True) == 0

    @pytest.mark.vcr()
    @pytest.mark.parametrize(
        "brightness,expected_state,expected_brightness",
        [(100, 1, 100), (0, 0, 100), (45, 1, 45)],
    )
    def test_set_brightness(
        self, dimmer, brightness, expected_state, expected_brightness
    ):
        dimmer.set_brightness(brightness)
        assert dimmer.get_state(force_update=True) == expected_state
        assert dimmer.get_brightness() == expected_brightness

    @pytest.mark.vcr()
    def test_brightness_on_startup(self, dimmer):
        dimmer.on()
        assert dimmer.get_brightness() != 0

    def test_subscription_update_brightness(self, dimmer):
        # Invalid value fails gracefully.
        assert dimmer.subscription_update("Brightness", "invalid") is False

        assert dimmer.subscription_update("BinaryState", "1") is True
        assert dimmer.subscription_update("Brightness", "52") is True

        assert dimmer.get_state() == 1
        assert dimmer.get_brightness() == 52


class Test_PVT_OWRT_Dimmer_v1(Base, long_press_helpers.TestLongPress):
    """Tests for the WeMo Dimmer, hardware version v1."""

    @pytest.fixture
    def dimmer(self, vcr):
        with vcr.use_cassette("WeMo_WW_2.00.11453.PVT-OWRT-Dimmer"):
            return device_from_uuid_and_location(
                "uuid:Dimmer-1_0-SERIALNUMBER",
                "http://192.168.1.100:49153/setup.xml",
            )

    device = dimmer  # for TestLongPress


class Test_PVT_RTOS_Dimmer_v2(Base):
    """Tests for the WeMo Dimmer, hardware version v2."""

    @pytest.fixture
    def dimmer(self, vcr):
        with vcr.use_cassette("WEMO_WW_2.00.20110904.PVT-RTOS-DimmerV2"):
            return device_from_uuid_and_location(
                "uuid:Dimmer-2_0-SERIALNUMBER",
                "http://192.168.1.100:49153/setup.xml",
            )

    @pytest.mark.vcr()
    def test_is_subscribed(self, dimmer, subscription_registry):
        subscription_registry.register(dimmer)
        path = list(subscription_registry._subscription_paths)[0]

        # Wait for registry to be ready to make sure the Dimmer device has
        # been registered.
        ready = threading.Event()
        subscription_registry._sched.enter(0.1, 0, ready.set)
        ready.wait()

        # Subscribe to all events.
        subscription_registry.on(
            dimmer, None, lambda a, b, c: dimmer.subscription_update(b, c)
        )

        # is_subscribed returns False when the device is On.
        subscription_registry.event(
            dimmer,
            EVENT_TYPE_BINARY_STATE,
            "1",  # Dimmer is On.
            path,
        )
        assert dimmer.get_state() == 1
        assert subscription_registry.is_subscribed(dimmer) is False

        # is_subscribed returns True when the device is Off.
        subscription_registry.event(
            dimmer,
            EVENT_TYPE_BINARY_STATE,
            "0",  # Dimmer is Off.
            path,
        )
        assert dimmer.get_state() == 0
        assert subscription_registry.is_subscribed(dimmer)
