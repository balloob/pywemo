"""Exceptions raised by pywemo."""


class PyWeMoException(Exception):
    """Base exception class for pyWeMo exceptions."""


class ActionException(PyWeMoException):
    """Generic exceptions when dealing with SOAP request Actions."""


class SOAPFault(ActionException):
    """Raised when the SOAP response contains a Fault message."""

    def __init__(self, fault_element):
        """Initialize from a SOAP Fault lxml.etree Element."""
        upnp_error_prefix = (
            "detail"
            "/{urn:schemas-upnp-org:control-1-0}UPnPError/"
            "{urn:schemas-upnp-org:control-1-0}"
        )
        self.fault_code = fault_element.findtext("faultcode")
        self.fault_string = fault_element.findtext("faultstring")
        self.error_code = fault_element.findtext(
            f"{upnp_error_prefix}errorCode"
        )
        self.error_description = fault_element.findtext(
            f"{upnp_error_prefix}errorDescription"
        )
        super().__init__(
            f"SOAP Fault {self.fault_code}:{self.fault_string}, "
            f"{self.error_code}:{self.error_description}"
        )


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


class RulesDbError(Exception):
    """Base class for errors related to the Rules database."""


class RulesDbQueryError(RulesDbError):
    """Exception when querying the rules database."""
