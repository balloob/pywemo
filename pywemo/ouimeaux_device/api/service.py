"""Representation of Services and Actions for WeMo devices."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable
from urllib.parse import urljoin, urlparse

import urllib3
from lxml import etree as et

from pywemo.exceptions import (
    ActionException,
    HTTPException,
    HTTPNotOkException,
    InvalidSchemaError,
    MissingServiceError,
    SOAPFault,
)

from .wemo_services import WeMoAllActionsMixin
from .xsd_types import ActionProperties, ServiceDescription, ServiceProperties

if TYPE_CHECKING:
    from .. import Device

LOG = logging.getLogger(__name__)
REQUESTS_TIMEOUT = 10

REQUEST_TEMPLATE = """
<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<u:{action} xmlns:u="{service}">
{args}
</u:{action}>
</s:Body>
</s:Envelope>
"""  # noqa: E501


class Session:
    """HTTP session with device.

    The Session provides timeouts and retries by default. The default
    parameters were chosen to provide for 3 attempts within 10 seconds, and
    further attempts beyond that to reestablish a link with the device without
    needing to attempt rediscovery. Retries continue for the duration that it
    would take the device to reboot, at which point reconnect_with_device
    should be able to find the device again by probing.

    It is important to not be too agressive with retries. The WeMo devices only
    have a small number of threads for servicing requests. If the timeout is
    too short or the retries are too frequent, it is easy to overwhelm the
    device with too many requests. This can result in a device that crashes
    and is unavailable until it reboots.

    The `urllib3` library is used as it provides better timeout/retry support
    than the `requests` library. Specifically, the `urllib3` library will
    retry in the case where the response body is not returned within timeout
    seconds. The `requests` library does not support retries of fetching the
    response body and will raise an exception if fetching the response body
    takes longer than the timeout.

    Since much of pywemo is built atop the requests library, a `content`
    field on HTTPResponse is populated from the `data` field.
    """

    retries: int | urllib3.Retry | None = urllib3.Retry(
        total=6, backoff_factor=1.5, allowed_methods=["GET", "POST"]
    )
    """Retry strategy for requests that fail."""

    timeout = 3.0
    """Seconds that a request can be idle before retrying."""

    def __init__(
        self,
        url: str,
        retries: int | urllib3.Retry | None = None,
        timeout: float | None = None,
    ) -> None:
        """Create a session with the specified default parameters."""
        self.url = url
        if retries is not None:
            self.retries = retries
        if timeout is not None:
            self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        retries: int | urllib3.Retry | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> urllib3.HTTPResponse:
        """Send request and gather response.

        A non-200 response code will result in a HTTPException
        exception.

        Args:
            method: HTTP method/verb to use for request (ex: 'GET' or 'POST').
            url: URL to to connect to.
            retries: Number of retries, or urllib3.Retry instance.
            timeout: Timeout in seconds for each request attempt.
            kwargs: Additional arguments for urllib3 pool.request(**kwargs).

        Raises:
            HTTPNotOkException: when the response code is not 200.
            HTTPException: for any urllib3 exception.
        """
        if retries is None:
            retries = self.retries
        if timeout is None:
            timeout = self.timeout

        # Create and destroy the pool each time. WeMo devices do not support
        # http keep-alive. Forcing the pool to be destroyed ensures that the
        # http connection is also closed. This avoids tying up TCP sessions
        # on the device.
        with urllib3.PoolManager(retries=retries, timeout=timeout) as pool:
            response: urllib3.HTTPResponse
            try:
                response = pool.request(method=method, url=url, **kwargs)
                if response.status != 200:
                    raise HTTPNotOkException(
                        f"Received status {response.status} for {url}"
                    )
            except urllib3.exceptions.HTTPError as err:
                raise HTTPException(err) from err
            # For `requests` compatibility.
            response.content = response.data  # type: ignore
            return response

    def get(self, url: str, **kwargs: Any) -> urllib3.HTTPResponse:
        """HTTP GET request."""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> urllib3.HTTPResponse:
        """HTTP POST request."""
        return self.request("POST", url, **kwargs)

    def urljoin(self, path: str) -> str:
        """Build an absolute URL from a path."""
        return urljoin(self._url, path)

    @property
    def url(self) -> str:
        """Return the current URL for the session."""
        return self._url

    @url.setter
    def url(self, url: str) -> str:
        """Update the URL for the session."""
        parsed_url = urlparse(url)
        self._url = parsed_url.geturl()
        self._port = parsed_url.port or 80
        self._host = parsed_url.hostname or ""
        return url

    @property
    def port(self) -> int:
        """TCP port associated with this session."""
        return self._port

    @property
    def host(self) -> str:
        """Hostname associated with this session."""
        return self._host


class Action:
    """Representation of an Action for a WeMo device."""

    soap_action_timeout_override = {
        "urn:Belkin:service:bridge:1#AddDevice": 30,
        "urn:Belkin:service:bridge:1#OpenNetwork": 30,
        "urn:Belkin:service:WiFiSetup:1#GetApList": 10,
    }
    """A few actions take longer than the default timeout. Override the default
    timeout value for those actions."""

    max_rediscovery_attempts = 3

    def __init__(
        self, service: Service, action_config: ActionProperties
    ) -> None:
        """Create an instance of an Action."""
        self.name = action_config.name
        self.service = service
        self.soap_action = f"{service.serviceType}#{self.name}"
        self.headers = {
            "Content-Type": "text/xml",
            "SOAPACTION": f'"{self.soap_action}"',
        }

        self.args = [
            arg.name
            for arg in action_config.arguments
            if arg.direction.lower() != "out"
        ]
        self.returns = [
            arg.name
            for arg in action_config.arguments
            if arg.direction.lower() == "out"
        ]

    def __call__(
        self, *, pywemo_timeout: float | None = None, **kwargs: Any
    ) -> dict[str, str]:
        """Representations a method or function call."""
        arglist = "\n".join(
            f"<{arg}>{value}</{arg}>" for arg, value in kwargs.items()
        )
        body = REQUEST_TEMPLATE.format(
            action=self.name, service=self.service.serviceType, args=arglist
        ).strip()
        timeout = pywemo_timeout or self.soap_action_timeout_override.get(
            self.soap_action
        )
        last_exception = None

        for attempt in range(self.max_rediscovery_attempts):
            session = self.service.device.session
            try:
                response = session.post(
                    self.service.controlURL,
                    headers=self.headers,
                    body=body,
                    timeout=timeout,
                )
            except HTTPException as err:
                LOG.warning(
                    "Error communicating with %s at %s:%i, %r retry %i",
                    self.service.device.name,
                    session.host,
                    session.port,
                    err,
                    attempt,
                )
                last_exception = err
            else:
                envelope = et.fromstring(
                    response.data, parser=et.XMLParser(resolve_entities=False)
                )
                body_element = list(envelope)[0]
                response_element = list(body_element)[0]
                if (
                    response_element.tag
                    == "{http://schemas.xmlsoap.org/soap/envelope/}Fault"
                ):
                    raise SOAPFault(
                        f"Error calling {self.soap_action}",
                        fault_element=response_element,
                    )
                return {
                    response_item.tag: response_item.text or ""
                    for response_item in response_element
                }

            self.service.device.reconnect_with_device()

        msg = (
            f"Error communicating with {self.service.device.name} after "
            f"{self.max_rediscovery_attempts} attempts. Giving up."
        )
        LOG.error(msg)
        raise ActionException(msg) from last_exception

    def __repr__(self) -> str:
        """Return a string representation of the Action."""
        return f"<Action {self.name}({', '.join(self.args)})>"


class Service(WeMoAllActionsMixin):
    """Representation of a service for a WeMo device."""

    def __init__(self, device: "Device", service: ServiceProperties) -> None:
        """Create an instance of a Service."""
        self.device = device
        self._config = service
        self.name = self.serviceType.split(":")[-2]
        self.actions = {}

        url = device.session.urljoin(service.description_url)
        xml = device.session.get(url).data

        try:
            scpd = ServiceDescription.from_xml(xml)
        except InvalidSchemaError:
            LOG.debug("Received invalid schema from %s: %r", url, xml)
            raise

        for action in scpd.actions:
            act = Action(self, action)
            self.actions[act.name] = act
            setattr(self, act.name, act)

    @property
    def controlURL(self) -> str:  # pylint: disable=invalid-name
        """Get the controlURL for interacting with this Service."""
        return self.device.session.urljoin(self._config.control_url)

    @property
    def eventSubURL(self) -> str:  # pylint: disable=invalid-name
        """Get the eventSubURL for interacting with this Service."""
        return self.device.session.urljoin(self._config.event_subscription_url)

    @property
    def serviceType(self) -> str:  # pylint: disable=invalid-name
        """Get the type of this Service."""
        return self._config.service_type

    def __repr__(self) -> str:
        """Return a string representation of the Service."""
        return f"<Service {self.name}({', '.join(self.actions)})>"


@dataclass(frozen=True)
class RequiredService:
    """Specifies the service name and actions that are required for a class."""

    name: str
    actions: list[str]


class RequiredServicesMixin:  # pylint: disable=too-few-public-methods
    """Provide and check for required services."""

    @property
    def _required_services(self) -> list[RequiredService]:
        return []

    def _check_required_services(self, services: Iterable[Service]) -> None:
        """Validate that all required services are found."""
        all_services: dict[str, set[str]] = defaultdict(set)
        for supported_service in services:
            all_services[supported_service.name].update(
                supported_service.actions
            )

        missing_actions: dict[str, set[str]] = defaultdict(set)

        for service in self._required_services:
            if service.name not in all_services:
                missing_actions[service.name].update(service.actions)
                continue
            service_actions = all_services[service.name]
            for action in service.actions:
                if action not in service_actions:
                    missing_actions[service.name].add(action)

        if missing_actions:
            error_str = ", ".join(
                f"{service}({', '.join(methods)})"
                for service, methods in missing_actions.items()
            )
            raise MissingServiceError(
                f"Missing required services/methods: {error_str}"
            )
