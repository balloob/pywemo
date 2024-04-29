"""Exceptions raised by pywemo."""

from __future__ import annotations

from lxml import etree as et


class PyWeMoException(Exception):
    """Base exception class for pyWeMo exceptions."""


class ActionException(PyWeMoException):
    """Generic exceptions when dealing with SOAP request Actions."""


class SOAPFault(ActionException):
    """Raised when the SOAP response contains a Fault message."""

    fault_code: str = ""
    fault_string: str = ""
    error_code: str = ""
    error_description: str = ""

    def __init__(
        self, message: str = "", fault_element: et._Element | None = None
    ) -> None:
        """Initialize from a SOAP Fault lxml.etree Element."""
        details = ""
        if fault_element is not None:
            upnp_error_prefix = (
                "detail"
                "/{urn:schemas-upnp-org:control-1-0}UPnPError/"
                "{urn:schemas-upnp-org:control-1-0}"
            )
            self.fault_code = fault_element.findtext("faultcode", "")
            self.fault_string = fault_element.findtext("faultstring", "")
            self.error_code = fault_element.findtext(
                f"{upnp_error_prefix}errorCode", ""
            )
            self.error_description = fault_element.findtext(
                f"{upnp_error_prefix}errorDescription", ""
            )
            details = (
                f" SOAP Fault {self.fault_code}:{self.fault_string}, "
                f"{self.error_code}:{self.error_description}"
            )
        super().__init__(f"{message}{details}")


class SubscriptionRegistryFailed(PyWeMoException):
    """General exceptions related to the subscription registry."""


class UnknownService(PyWeMoException):
    """Exception raised when a non-existent service is called."""


class ResetException(PyWeMoException):
    """Exception raised when reset fails."""


class SetupException(PyWeMoException):
    """Exception raised when setup fails."""


class APNotFound(SetupException):
    """Exception raised when the AP requested is not found."""


class ShortPassword(SetupException):
    """Exception raised when a password is too short (<8 characters)."""


class HTTPException(PyWeMoException):
    """HTTP request to the device failed."""


class HTTPNotOkException(HTTPException):
    """Raised when a non-200 status is returned."""


class RulesDbError(PyWeMoException):
    """Base class for errors related to the Rules database."""


class RulesDbQueryError(RulesDbError):
    """Exception when querying the rules database."""


class InvalidSchemaError(PyWeMoException):
    """Raised when an unexpected XML response is received."""


class MissingServiceError(PyWeMoException):
    """All required services were not found in the device schema."""
