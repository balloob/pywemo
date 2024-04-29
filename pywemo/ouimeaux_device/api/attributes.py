"""Attribute device helpers."""

from __future__ import annotations

import logging
from typing import Any, get_type_hints

from lxml import etree as et

from ..switch import Switch
from .service import RequiredService
from .xsd_types import quote_xml

LOG = logging.getLogger(__name__)


class AttributeDevice(Switch):
    """Handles all parsing/getting/setting of attribute lists.

    This is intended to be used as the base class for all devices that support
    the deviceevent.GetAttributes() method.

    Subclasses can use the _attributes property to fetch the string values of
    all attributes. Subclasses must provide the name of the property to use
    for self._state in a property named _state_property. Subclasses must also
    define a TypedDict to hold the attributes and add the TypedDict subclass as
    a type hint for the _attributes property of the class.
    """

    EVENT_TYPE_ATTRIBUTE_LIST = "attributeList"

    _state_property: str

    _attr_name = "_attributes"
    """Name of the TypedDict attribute that holds values for this device."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create a Attributes device."""
        assert isinstance(self._state_property, str)
        setattr(self, self._attr_name, {})
        class_hints = get_type_hints(type(self))
        assert (attr_type := class_hints.get(self._attr_name)) is not None
        self._attribute_type_hints = get_type_hints(attr_type)
        super().__init__(*args, **kwargs)
        self.update_attributes()

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(
                name="deviceevent", actions=["GetAttributes", "SetAttributes"]
            ),
        ]

    def _update_attributes_dict(self, xml_blob: str) -> None:
        xml_blob = "<attributes>" + xml_blob + "</attributes>"
        xml_blob = xml_blob.replace("&gt;", ">")
        xml_blob = xml_blob.replace("&lt;", "<")

        for attribute in et.fromstring(
            xml_blob, parser=et.XMLParser(resolve_entities=False)
        ):
            if len(attribute) < 2:
                raise ValueError(
                    f"Too few elements: {et.tostring(attribute).decode()}"
                )
            if (key := attribute[0].text) is None:
                raise ValueError(
                    f"Key is not present: {et.tostring(attribute[0]).decode()}"
                )
            if (value := attribute[1].text) is None:
                raise ValueError(
                    "Value is not present: "
                    f"{et.tostring(attribute[1]).decode()}"
                )

            if (constructor := self._attribute_type_hints.get(key)) is None:
                continue  # Ignore unexpected attributes
            try:
                getattr(self, self._attr_name)[key] = constructor(value)
            except (TypeError, ValueError) as err:
                raise ValueError(
                    f"Unexpected value for {key}: {value}"
                ) from err

        state: int | None = getattr(self, self._state_property)
        self._state = state

    def update_attributes(self) -> None:
        """Request state from device."""
        resp = self.deviceevent.GetAttributes().get(
            self.EVENT_TYPE_ATTRIBUTE_LIST
        )
        assert resp is not None
        self._update_attributes_dict(resp)

    def subscription_update(self, _type: str, _params: str) -> bool:
        """Handle subscription push-events from device."""
        if _type == self.EVENT_TYPE_ATTRIBUTE_LIST:
            try:
                self._update_attributes_dict(_params)
            except (et.XMLSyntaxError, ValueError) as err:
                LOG.error(
                    "Unexpected %s value `%s` for device %s: %s",
                    self.EVENT_TYPE_ATTRIBUTE_LIST,
                    _params,
                    self.name,
                    repr(err),
                )
            return True

        return super().subscription_update(_type, _params)

    def get_state(self, force_update: bool = False) -> int:
        """Return 0 if off and 1 if on."""
        if force_update or self._state is None:
            self.update_attributes()

        assert self._state is not None
        return self._state

    def _set_attributes(self, *args: tuple[str, str | int | float]) -> None:
        """Set the specified attributes on the device."""
        attribute_xml = "</attribute><attribute>".join(
            f"<name>{name}</name><value>{value}</value>"
            for name, value in args
        )
        attribute_xml = f"<attribute>{attribute_xml}</attribute>"
        self.deviceevent.SetAttributes(attributeList=quote_xml(attribute_xml))

        # Refresh the device state
        self.get_state(True)
