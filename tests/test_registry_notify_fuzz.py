"""Exercise the NOTIFY subscription http endpoint."""

from __future__ import annotations

import contextlib
import string
import sys
import unittest.mock as mock

from tests import atheris_stub as atheris

with atheris.instrument_imports():
    from lxml import etree
    import pywemo

import pytest
import requests
from hypothesis import example, given
from hypothesis import strategies as st

from tests.ouimeaux_device.test_device import mocked_requests_get

MOCK_SERVICE_RETURN_VALUES = {
    "bridge": {
        "GetEndDevicesWithStatus": {
            "DeviceLists": (
                '<?xml version="1.0" encoding="utf-8"?>'
                "<DeviceLists>"
                "<DeviceList>"
                "<DeviceListType>Paired</DeviceListType>"
                "<DeviceInfos>"
                "<DeviceInfo>"
                "<DeviceIndex>0</DeviceIndex>"
                '<DeviceID available="NO">0017880108DA898B</DeviceID>'
                "<FriendlyName>Living Room Couch Light</FriendlyName>"
                "<IconVersion>1</IconVersion>"
                "<FirmwareVersion>02</FirmwareVersion>"
                "<CapabilityIDs>10006,10008,30008,30009,3000A</CapabilityIDs>"
                "<CapabilityValue>,,,,</CapabilityValue>"
                "<IsGroupAction>NO</IsGroupAction>"
                "<LastEventTimeStamp>0</LastEventTimeStamp>"
                "<Manufacturer>Philips</Manufacturer>"
                "<ModelCode>LWA008</ModelCode>"
                "<productName>lighting</productName>"
                "<WeMoCertified>NO</WeMoCertified>"
                "</DeviceInfo>"
                "<DeviceInfo>"
                "<DeviceIndex>3</DeviceIndex>"
                '<DeviceID available="YES">F0D1B8000001420C</DeviceID>'
                "<FriendlyName>RGBW Light</FriendlyName>"
                "<IconVersion>1</IconVersion>"
                "<FirmwareVersion>01</FirmwareVersion>"
                "<CapabilityIDs>"
                "10006,10008,10300,30008,30009,3000A,30301"
                "</CapabilityIDs>"
                "<CapabilityValue>"
                "0,120:0,11534:42859:0,,,,478:0"
                "</CapabilityValue>"
                "<IsGroupAction>NO</IsGroupAction>"
                "<LastEventTimeStamp>0</LastEventTimeStamp>"
                "<Manufacturer>LEDVANCE</Manufacturer>"
                "<ModelCode>A19 RGBW</ModelCode>"
                "<productName>lighting</productName>"
                "<WeMoCertified>NO</WeMoCertified>"
                "</DeviceInfo>"
                "</DeviceInfos>"
                "<GroupInfos>"
                "<GroupInfo>"
                "<GroupID>12345678</GroupID>"
                "<GroupName>Testing Group</GroupName>"
                "<GroupCapabilityIDs>"
                "10006,10008,30008,30009,3000A"
                "</GroupCapabilityIDs>"
                "<GroupCapabilityValues>0,1:0,0:0,,</GroupCapabilityValues>"
                "<DeviceInfos>"
                "<DeviceInfo>"
                "<DeviceIndex>0</DeviceIndex>"
                '<DeviceID available="YES">94103EA2B277BD6E</DeviceID>'
                "<FriendlyName>Test Bulb 1</FriendlyName>"
                "<IconVersion>1</IconVersion>"
                "<FirmwareVersion>83</FirmwareVersion>"
                "<CapabilityIDs>10006,10008,30008,30009,3000A</CapabilityIDs>"
                "<CapabilityValue>1,1:0,,,</CapabilityValue>"
                "<IsGroupAction>YES</IsGroupAction>"
                "<LastEventTimeStamp>0</LastEventTimeStamp>"
                "<Manufacturer>MRVL</Manufacturer>"
                "<ModelCode>MZ100</ModelCode>"
                "<productName>Lighting</productName>"
                "<WeMoCertified>YES</WeMoCertified>"
                "</DeviceInfo>"
                "<DeviceInfo>"
                "<DeviceIndex>1</DeviceIndex>"
                '<DeviceID available="YES">94103EA2B27751AB</DeviceID>'
                "<FriendlyName>Test Bulb 2</FriendlyName>"
                "<IconVersion>1</IconVersion>"
                "<FirmwareVersion>83</FirmwareVersion>"
                "<CapabilityIDs>10006,10008,30008,30009,3000A</CapabilityIDs>"
                "<CapabilityValue>1,20:0,,,</CapabilityValue>"
                "<IsGroupAction>YES</IsGroupAction>"
                "<LastEventTimeStamp>0</LastEventTimeStamp>"
                "<Manufacturer>MRVL</Manufacturer>"
                "<ModelCode>MZ100</ModelCode>"
                "<productName>Lighting</productName>"
                "<WeMoCertified>YES</WeMoCertified>"
                "</DeviceInfo>"
                "</DeviceInfos>"
                "</GroupInfo>"
                "</GroupInfos>"
                "</DeviceList>"
                "</DeviceLists>"
            )
        }
    },
    "deviceevent": {
        "GetAttributes": {
            "attributeList": (
                "<attribute>"
                "<name>Switch</name>"
                "<value>1</value>"
                "</attribute>"
            )
        }
    },
    "basicevent": {
        "GetCrockpotState": {"cookedTime": "0", "mode": "0", "time": "0"}
    },
    "insight": {
        "GetInsightParams": {
            "InsightParams": (
                "8|1611105078|2607|0|12416|1209600|328|500|457600|69632638|95"
            )
        }
    },
}


@mock.patch("urllib3.PoolManager.request", side_effect=mocked_requests_get)
def make_device(device_class, *args):
    class WrappedDevice(device_class):
        @property
        def device_type(self) -> str:
            return device_class.__name__

        @property
        def name(self):
            return device_class.__name__

        @name.setter
        def name(self, name):
            pass

        def _check_required_services(self, services):
            for service in self._required_services:
                service_mock = mock.Mock()
                for action, return_value in MOCK_SERVICE_RETURN_VALUES.get(
                    service.name, {}
                ).items():
                    getattr(service_mock, action).return_value = return_value
                self.services[service.name] = service_mock
                setattr(self, service.name, service_mock)

    device = WrappedDevice("http://192.168.1.100:49158/setup.xml")
    device.session.url = "http://127.0.0.1:49158/"
    return device


DEVICES = {
    device.__name__: make_device(device)
    for device in (
        # All subclasses of pywemo.WeMoDevice.
        obj
        for obj in (getattr(pywemo, name) for name in dir(pywemo))
        if isinstance(obj, type)
        and issubclass(obj, pywemo.WeMoDevice)
        and obj != pywemo.WeMoDevice
    )
}
DEVICE_NAMES = sorted(DEVICES.keys())
REGISTRY = pywemo.SubscriptionRegistry()


@contextlib.contextmanager
def registry():
    REGISTRY.start()
    pywemo.subscribe.Subscription.scheduler_active = False
    try:
        yield REGISTRY
    finally:
        pywemo.subscribe.Subscription.scheduler_active = True
        REGISTRY.stop()


@pytest.fixture(scope="module", autouse=True)
def pytest_registry():
    with registry():
        yield


class ConvertChildrenToText:
    """Name type to signal that child elements will be converted to text."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        """Return value."""
        return self.value

    def __repr__(self) -> str:
        """Representation of ConvertChildrenToText."""
        return f"<{self.value}>"


class ElementWithAttributes:
    """Element with attributes."""

    def __init__(self, tag: str, attributes: dict[str, str]) -> None:
        self.tag = tag
        self.attributes = attributes

    def __str__(self) -> str:
        """Return tag name."""
        return self.tag

    def __repr__(self) -> str:
        """Representation of ElementWithAttributes."""
        attr = " ".join(
            f"{name}='{value}'" for name, value in self.attributes.items()
        )
        return f"<{self.tag} {attr}>"


PROPERTY_NAMES = st.one_of(
    st.sampled_from(
        [
            "attribute",
            "attributeList",
            "cookedTime",
            "mode",
            "name",
            "time",
            "value",
            "BinaryState",
            "CapabilityID",
            "CapabilityId",
            "CapabilityValue",
            "CurrentHumidity",
            "DesiredHumidity",
            "DeviceID",
            "DeviceStatus",
            "ExpiredFilterTime",
            "FanMode",
            "FilterLife",
            "InsightParams",
            "IsGroupAction",
            "LastEventTimeStamp",
            "LongPress",
            "Mode",
            "NoWater",
            "StateEvent",
            "StatusChange",
            "Switch",
            "Value",
            "WaterAdvise",
            ConvertChildrenToText("attributeList"),
            ConvertChildrenToText("StatusChange"),
        ],
    ),
    st.text(alphabet=string.ascii_letters, min_size=1),
)

PROPERTY_VALUES = st.one_of(
    st.sampled_from(["-1", "0", "1"]),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(
        alphabet=string.ascii_letters
        + string.digits
        + string.punctuation
        + " \t\r\n"
    ),
)


def toXml(properties):
    """Convert hypothesis generated properties into XML."""
    NS = pywemo.subscribe.NS
    root = etree.Element(f"{NS}propertyset", nsmap={"e": NS[1:-1]})
    elements = [(root, (f"{NS}property", [prop])) for prop in properties]
    text_convert_elements = []

    while elements:
        parent, (name, value_or_list) = elements.pop(0)
        child = etree.SubElement(parent, str(name))
        if isinstance(name, ElementWithAttributes):
            for key, value in name.attributes.items():
                child.set(key, value)
        if not isinstance(value_or_list, list):
            child.text = str(value_or_list)
            continue
        elements.extend((child, value) for value in value_or_list)
        if isinstance(name, ConvertChildrenToText):
            text_convert_elements.append(child)

    for element in reversed(text_convert_elements):
        text = b"".join(
            [etree.tostring(child) for child in element.iterchildren()]
        )
        element.clear()
        element.text = text

    return etree.tostring(root)


def status_change(names=PROPERTY_NAMES, values=PROPERTY_VALUES):
    """<StatusChange> XML for Bridge devices."""
    devices = st.sampled_from(
        [
            "0017880108DA898B",
            "F0D1B8000001420C",
            "94103EA2B277BD6E",
            "94103EA2B27751AB",
            "12345678",
        ]
    )
    capabilities = st.sampled_from(
        [
            "10006",
            "10008",
            "30008",
            "30009",
            "3000A",
            "10300",
            "30301",
        ]
    )
    device_id = st.sampled_from(
        [
            "DeviceID",
            ElementWithAttributes("DeviceID", {"available": "NO"}),
        ]
    )

    return st.tuples(
        st.just(ConvertChildrenToText("StatusChange")),
        st.lists(
            st.tuples(
                st.just("StateEvent") | names,
                st.lists(
                    st.tuples(device_id, devices | values)
                    | st.tuples(st.just("CapabilityId"), capabilities | values)
                    | st.tuples(st.just("Value"), values)
                    | st.tuples(names, values),
                    min_size=2,
                    max_size=4,
                ),
            ),
            max_size=1,
        ),
    )


def attribute_list(names=PROPERTY_NAMES, values=PROPERTY_VALUES):
    """<attributeList> for devices that support the attribute interface."""
    return st.tuples(
        st.just(ConvertChildrenToText("attributeList")),
        st.lists(
            st.tuples(
                st.just("attribute") | names,
                st.lists(
                    st.tuples(st.just("name"), names)
                    | st.tuples(st.just("values"), values)
                    | st.tuples(names, values),
                    max_size=3,
                ),
            ),
            max_size=4,
        ),
    )


def properties(names=PROPERTY_NAMES, values=PROPERTY_VALUES):
    """Generate properties for all device types."""
    return st.lists(
        st.tuples(
            names, values | st.lists(st.tuples(names, values), max_size=2)
        )
        | attribute_list(names, values)
        | status_change(names, values),
        max_size=5,
    )


@given(name=st.sampled_from(DEVICE_NAMES), properties=properties())
# Previous problem cases.
@example(name="Bridge", properties=[("StatusChange", "1")])
@example(
    name="Bridge",
    properties=[
        (
            ConvertChildrenToText("StatusChange"),
            [
                (
                    "StateEvent",
                    [
                        (
                            ElementWithAttributes(
                                "DeviceID", {"available": "NO"}
                            ),
                            "0017880108DA898B",
                        ),
                    ],
                )
            ],
        )
    ],
)
@example(
    name="Bridge",
    properties=[
        (
            ConvertChildrenToText("StatusChange"),
            [
                (
                    "StateEvent",
                    [
                        ("DeviceID", "0017880108DA898B"),
                        ("CapabilityId", "10008"),
                        ("Value", "-1"),
                    ],
                )
            ],
        )
    ],
)
@example(
    name="Bridge",
    properties=[
        (
            ConvertChildrenToText("StatusChange"),
            [
                (
                    "StateEvent",
                    [
                        ("DeviceID", "0017880108DA898B"),
                        ("CapabilityId", "10006"),
                        ("Value", "1"),
                    ],
                )
            ],
        )
    ],
)
@example(
    name="Bridge",
    properties=[
        (
            ConvertChildrenToText("StatusChange"),
            [
                (
                    "StateEvent",
                    [
                        ("DeviceID", "0017880108DA898B"),
                        ("CapabilityId", "10006"),
                        ("Value", "m50"),
                    ],
                )
            ],
        )
    ],
)
@example(
    name="Bridge",
    properties=[
        (
            ConvertChildrenToText("StatusChange"),
            [
                (
                    "StateEvent",
                    [
                        ("Value", "1"),
                        ("DeviceID", "F0D1B8000001420C"),
                        ("CapabilityId", "10008"),
                    ],
                )
            ],
        )
    ],
)
@example(
    name="Bridge",
    properties=[
        (
            ConvertChildrenToText("StatusChange"),
            [
                (
                    "StateEvent",
                    [
                        ("Value", "0"),
                        ("DeviceID", "F0D1B8000001420C"),
                        ("CapabilityId", "30301"),
                    ],
                )
            ],
        )
    ],
)
@example(name="CoffeeMaker", properties=[("attributeList", None)])
@example(name="CoffeeMaker", properties=[("attributeList", "<")])
@example(
    name="CoffeeMaker",
    properties=[
        (
            ConvertChildrenToText("attributeList"),
            [("attribute", [("name", "Mode"), ("values", "0.5")])],
        )
    ],
)
@example(name="CrockPot", properties=[("cookedTime", "'")])
@example(name="CrockPot", properties=[("mode", "'")])
@example(name="CrockPot", properties=[("time", "'")])
@example(name="Insight", properties=[("InsightParams", "1")])
@example(
    name="Maker",
    properties=[
        (
            ConvertChildrenToText("attributeList"),
            [("attribute", [("name", "attribute"), ("values", [])])],
        )
    ],
)
def test_notify(name, properties):
    device = DEVICES[name]
    REGISTRY.register(device)
    REGISTRY.on(device, None, lambda d, t, v: d.subscription_update(t, v))
    path = list(REGISTRY._subscription_paths)[0]
    try:
        response = requests.request(
            "NOTIFY",
            f"http://127.0.0.1:{REGISTRY.port}{path}",
            data=toXml(properties),
        )
        assert response.status_code == 200
    finally:
        REGISTRY.unregister(device)


if __name__ == "__main__":
    atheris.Setup(
        sys.argv,
        atheris.instrument_func(test_notify.hypothesis.fuzz_one_input),
    )
    with registry():
        atheris.Fuzz()
