"""Parsing and validation for Device/Service XML.

Provides a light wrapper around the generated device & service parsers. The
wrappers check for required fields and values. Default values are also
provided for optional fields. Clients of this module can expect that all
fields of the dataclass instances are fully populated and valid. Any parsing
or validation issues will result in InvalidSchemaError being raised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from lxml import etree as et

from pywemo.exceptions import InvalidSchemaError

from .xsd import device as device_parser
from .xsd import service as service_parser

LOG = logging.getLogger(__name__)


def quote_xml(xml: str) -> str:
    """Escape markup chars, but do not modify CDATA sections."""
    return device_parser.quote_xml(xml)  # type: ignore


@dataclass(frozen=True)
class ArgumentType:
    """Parsed service_parser.ArgumentType."""

    name: str
    direction: str

    @classmethod
    def from_argument(
        cls, argument: service_parser.ArgumentType
    ) -> ArgumentType:
        """Parse and validate the service_parser.ArgumentType."""
        return cls(
            name=_get_element_text(argument, "name", ""),
            direction=_get_element_text(argument, "direction", ""),
        )


@dataclass(frozen=True)
class ActionProperties:
    """Parsed service_parser.ActionType."""

    name: str
    arguments: list[ArgumentType]

    @classmethod
    def from_action(
        cls, action: service_parser.ActionType
    ) -> ActionProperties:
        """Parse and validate the service_parser.ActionType."""
        arguments: list[service_parser.ArgumentType] = []
        if action.argumentList and action.argumentList.argument:
            arguments = action.argumentList.argument

        return cls(
            name=_get_element_text(action, "name"),
            arguments=[
                ArgumentType.from_argument(argument) for argument in arguments
            ],
        )


@dataclass(frozen=True)
class ServiceDescription:
    """Parsed service_parser.scpd."""

    actions: list[ActionProperties]

    @classmethod
    def from_xml(cls, service_xml_content: bytes) -> ServiceDescription:
        """Parse and validate the service_parser.scpd."""
        try:
            scpd = service_parser.parseString(  # type: ignore
                service_xml_content, silence=True, print_warnings=False
            )
        except Exception as err:
            raise InvalidSchemaError("Could not parse schema") from err

        if scpd.actionList and scpd.actionList.action:
            actions = scpd.actionList.action
        else:
            actions = []

        return cls(
            actions=[
                ActionProperties.from_action(action) for action in actions
            ]
        )


@dataclass(frozen=True)
class ServiceProperties:
    """Parsed device_parser.serviceType."""

    service_type: str
    service_id: str
    description_url: str
    control_url: str
    event_subscription_url: str

    @classmethod
    def from_service(
        cls, service: device_parser.serviceType
    ) -> ServiceProperties:
        """Parse and validate the device_parser.serviceType."""
        return cls(
            service_type=_get_element_text(service, "serviceType"),
            service_id=_get_element_text(service, "serviceId"),
            description_url=_get_element_text(service, "SCPDURL"),
            control_url=_get_element_text(service, "controlURL"),
            event_subscription_url=_get_element_text(service, "eventSubURL"),
        )


@dataclass(frozen=True)
class DeviceDescription:  # pylint: disable=too-many-instance-attributes
    """Device properties from the DeviceType xsd type."""

    firmware_version: str
    name: str
    mac: str
    manufacturer: str
    model: str
    model_name: str
    serial_number: str
    udn: str
    _config_any: Dict[str, str]
    _device_type: str
    _services: List[ServiceProperties]

    @staticmethod
    def dict_from_xml(setup_xml_content: bytes) -> dict[str, Any]:
        """Parse and validate the DeviceType xsd type."""
        try:
            root = device_parser.parseString(  # type: ignore
                setup_xml_content, silence=True, print_warnings=False
            )
        except Exception as err:
            raise InvalidSchemaError("Could not parse schema") from err

        device = root.get_device()
        if device is None:
            raise InvalidSchemaError("Missing root.device element")
        manufacturer = _get_element_text(device, "manufacturer")
        if manufacturer != "Belkin International Inc.":
            raise InvalidSchemaError(
                f"Unexpected manufacturer: {manufacturer}"
            )

        if device.anytypeobjs_:
            xs_any = (
                et.fromstring(
                    extra, parser=et.XMLParser(resolve_entities=False)
                )
                for extra in device.anytypeobjs_
            )
            config_any = {
                et.QName(tag).localname: tag.text.strip()
                for tag in xs_any
                if tag.text and tag.text.strip()
            }
        else:
            config_any = {}

        if device.serviceList and device.serviceList.service:
            service_list = device.serviceList.service
        else:
            service_list = []

        return {
            "firmware_version": config_any.get("firmwareVersion", ""),
            "name": _get_element_text(device, "friendlyName"),
            "mac": _get_element_text(device, "macAddress", ""),
            "manufacturer": manufacturer,
            "model": _get_element_text(device, "modelDescription", ""),
            "model_name": _get_element_text(device, "modelName"),
            "serial_number": _get_element_text(device, "serialNumber", ""),
            "udn": _get_element_text(device, "UDN"),
            "_config_any": config_any,
            "_device_type": _get_element_text(device, "deviceType"),
            "_services": [
                ServiceProperties.from_service(service)
                for service in service_list
            ],
        }

    @classmethod
    def from_xml(cls, setup_xml_content: bytes) -> DeviceDescription:
        """Parse and validate the DeviceType xsd type."""
        return cls(**cls.dict_from_xml(setup_xml_content))

    def __hash__(self) -> int:
        """Hash only the required elements from the xsd."""
        return hash((self.name, self.manufacturer, self.model_name, self.udn))


def _get_element_text(
    parent_element: Any, element_name: str, default_value: str | None = None
) -> str:
    """Extract text from a sub-element.

    If the sub-element is not found:
      1. If a `default_value` is provided, that will be returned.
      2. If no `default_value` is provided, raises InvalidSchemaError.

    Use #1 for optional elements and use #2 for required elements.
    """
    text: str | None = getattr(parent_element, f"get_{element_name}")()
    if text is None or not text:
        if default_value is not None:
            return default_value
        raise InvalidSchemaError(f"Missing element: {element_name}")
    return text.strip()
