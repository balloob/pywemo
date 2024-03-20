"""Methods to make changes to the long press rules for a device.

Wemo devices store a database of rules that configure actions for the device. A
long press rule is activated when the button on the device is pressed for 2
seconds. A person can press the button for 2 seconds and, based on the rules
configured for the device, it will turn on/off/toggle other Wemo devices on the
network. The methods in this mixin allow editing of the devices that are
controlled by a long press.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Iterable, no_type_check

from .rules_db import RuleDevicesRow, RulesDb, RulesRow, rules_db_from_device
from .service import RequiredService, RequiredServicesMixin

LOG = logging.getLogger(__name__)

# RulesRow.Type values.
RULE_TYPE_LONG_PRESS = "Long Press"

VIRTUAL_DEVICE_UDN = "uuid:Socket-1_0-PyWemoVirtualDevice"


class ActionType(Enum):
    """Action to perform when a long press rule is triggered."""

    TOGGLE = 2.0
    ON = 1.0
    OFF = 0.0


@no_type_check
def ensure_long_press_rule_exists(
    rules_db: RulesDb, device_name: str, device_udn: str
) -> RulesRow:
    """Ensure that a long press rule exists and is enabled for the device.

    Returns the long press rule.
    """
    current_rules = rules_db.rules_for_device(rule_type=RULE_TYPE_LONG_PRESS)
    for rule, _ in current_rules:
        if rule.State != "1":
            LOG.info("Enabling long press rule for device %s", device_name)
            rule.State = "1"
            rule.update_db(rules_db.cursor())
        return rule

    LOG.info("Adding long press rule for device %s", device_name)
    rule_orders = (
        r.RuleOrder for r in rules_db.rules.values() if r.RuleOrder is not None
    )
    new_rule = RulesRow(
        Name=f"{device_name} Long Press Rule",
        Type=RULE_TYPE_LONG_PRESS,
        RuleOrder=max(rule_orders, default=-1) + 1,
        StartDate="12201982",
        EndDate="07301982",
        State="1",
        Sync="NOSYNC",
    )
    rules_db.add_rule(new_rule)
    rules_db.add_rule_devices(
        RuleDevicesRow(
            RuleID=new_rule.RuleID,  # pylint: disable=no-member
            DeviceID=device_udn,
            GroupID=0,
            DayID=-1,
            StartTime=60,
            RuleDuration=86340,
            StartAction=ActionType.TOGGLE.value,
            EndAction=-1.0,
            SensorDuration=-1,
            Type=-1,
            Value=-1,
            Level=-1,
            ZBCapabilityStart="",
            ZBCapabilityEnd="",
            OnModeOffset=-1,
            OffModeOffset=-1,
            CountdownTime=-1,
            EndTime=86400,
        )
    )
    return new_rule


class LongPressMixin(RequiredServicesMixin):
    """Methods to make changes to the long press rules for a device."""

    EVENT_TYPE_LONG_PRESS = "LongPress"

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(name="rules", actions=["FetchRules", "StoreRules"])
        ]

    @no_type_check
    def list_long_press_udns(self) -> frozenset[str]:
        """Return a list of device UDNs that are configured for long press."""
        devices = []
        with rules_db_from_device(self) as rules_db:
            for rule, _ in rules_db.rules_for_device(
                rule_type=RULE_TYPE_LONG_PRESS
            ):
                devices.extend(rules_db.get_target_devices_for_rule(rule))
        return frozenset(devices)

    @no_type_check
    def add_long_press_udns(self, device_udns: Iterable[str]) -> None:
        """Add a list of device UDNs to be configured for long press."""
        with rules_db_from_device(self) as rules_db:
            rule = ensure_long_press_rule_exists(rules_db, self.name, self.udn)
            for udn in device_udns:
                if not udn:
                    continue
                if udn not in rules_db.get_target_devices_for_rule(rule):
                    rules_db.add_target_device_to_rule(rule, udn)

    @no_type_check
    def remove_long_press_udns(self, device_udns: Iterable[str]) -> None:
        """Remove a list of device UDNs from the long press configuration."""
        with rules_db_from_device(self) as rules_db:
            for rule, _ in rules_db.rules_for_device(
                rule_type=RULE_TYPE_LONG_PRESS
            ):
                for udn in device_udns:
                    if udn in rules_db.get_target_devices_for_rule(rule):
                        rules_db.remove_target_device_from_rule(rule, udn)

    @no_type_check
    def get_long_press_action(self) -> ActionType | None:
        """Fetch the ActionType for the long press rule.

        Will return None if no long press rule is configured for the device.
        """
        with rules_db_from_device(self) as rules_db:
            for _, device in rules_db.rules_for_device(
                rule_type=RULE_TYPE_LONG_PRESS
            ):
                return ActionType(device.StartAction)
        return None

    @no_type_check
    def set_long_press_action(self, action: ActionType) -> None:
        """Set the ActionType for the long press rule."""
        with rules_db_from_device(self) as rules_db:
            ensure_long_press_rule_exists(rules_db, self.name, self.udn)
            for _, device in rules_db.rules_for_device(
                rule_type=RULE_TYPE_LONG_PRESS
            ):
                device.StartAction = action.value

    def ensure_long_press_virtual_device(self) -> None:
        """Configure the device to notify pywemo when a long-press happens.

        The ensure_long_press_virtual_device method ensures that the pywemo
        virtual device is configured in the rules database for when a long
        press rule is triggered.
        """
        self.add_long_press_udns([VIRTUAL_DEVICE_UDN])

    def remove_long_press_virtual_device(self) -> None:
        """Remove the pywemo virtual device from the long press."""
        self.remove_long_press_udns([VIRTUAL_DEVICE_UDN])
