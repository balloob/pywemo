"""Module to listen for WeMo events.

Example usage:

```python
import pywemo
# The SubscriptionRegistry maintains push subscriptions to each endpoint
# of a device.
registry = pywemo.SubscriptionRegistry()
registry.start()

device = ... # See example of discovering devices in the pywemo module.

# Start subscribing to push notifications of state changes.
registry.register(device)

def push_notification(device, event, params):
    '''Notify device of state change and get new device state.'''
    processed_update = device.subscription_update(event, params)
    state = device.get_state(force_update=not processed_update)
    print(f"Device state: {state}")

# Register a callback to receive state push notifications.
registry.on(device, None, push_notification)

# Do some work.
# time.sleep(60)

# Stop the registry
registry.unregister(device)
registry.stop()
```
"""

from __future__ import annotations

import collections
import logging
import os
import sched
import secrets
import threading
import time
import warnings
from collections.abc import Iterable, MutableMapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import requests
from lxml import etree as et

from .exceptions import SubscriptionRegistryFailed
from .ouimeaux_device import Device
from .ouimeaux_device.api.long_press import VIRTUAL_DEVICE_UDN
from .ouimeaux_device.api.service import REQUESTS_TIMEOUT
from .ouimeaux_device.dimmer import DimmerV2
from .ouimeaux_device.insight import Insight
from .util import get_callback_address

# Subscription event types.
EVENT_TYPE_BINARY_STATE = "BinaryState"
EVENT_TYPE_INSIGHT_PARAMS = "InsightParams"
EVENT_TYPE_LONG_PRESS = "LongPress"

LOG = logging.getLogger(__name__)
NS = "{urn:schemas-upnp-org:event-1-0}"
RESPONSE_SUCCESS = "<html><body><h1>200 OK</h1></body></html>"
RESPONSE_NOT_FOUND = "<html><body><h1>404 Not Found</h1></body></html>"
SUBSCRIPTION_RETRY = 60

VIRTUAL_SETUP_XML = f"""<?xml version="1.0"?>
<root xmlns="urn:Belkin:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:Belkin:device:controllee:1</deviceType>
    <friendlyName>pywemo virtual device</friendlyName>
    <manufacturer>pywemo</manufacturer>
    <manufacturerURL>https://github.com/pywemo/pywemo</manufacturerURL>
    <modelDescription>pywemo virtual device</modelDescription>
    <modelName>Socket</modelName>
    <modelNumber>1.0</modelNumber>
    <hwVersion>v1</hwVersion>
    <modelURL>http://www.belkin.com/plugin/</modelURL>
    <serialNumber>PyWemoVirtualDevice</serialNumber>
    <firmwareVersion>WeMo_US_2.00.2769.PVT</firmwareVersion>
    <UDN>{VIRTUAL_DEVICE_UDN}</UDN>
    <binaryState>0</binaryState>
    <serviceList>
      <service>
        <serviceType>urn:Belkin:service:basicevent:1</serviceType>
        <serviceId>urn:Belkin:serviceId:basicevent1</serviceId>
        <controlURL>/upnp/control/basicevent1</controlURL>
        <eventSubURL>/upnp/event/basicevent1</eventSubURL>
        <SCPDURL>/eventservice.xml</SCPDURL>
      </service>
    </serviceList>
  <presentationURL>/pluginpres.html</presentationURL>
</device>
</root>"""

SOAP_ACTION_RESPONSE = {
    '"urn:Belkin:service:basicevent:1#GetBinaryState"': """<s:Envelope
  xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
  s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
  <u:GetBinaryStateResponse xmlns:u="urn:Belkin:service:basicevent:1">
  <BinaryState>0</BinaryState>
  </u:GetBinaryStateResponse>
</s:Body></s:Envelope>""",
    '"urn:Belkin:service:basicevent:1#SetBinaryState"': """<s:Envelope
  xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
  s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
  <u:SetBinaryStateResponse xmlns:u="urn:Belkin:service:basicevent:1">
    <BinaryState>0</BinaryState>
  </u:SetBinaryStateResponse>
</s:Body></s:Envelope>""",
}

ERROR_SOAP_ACTION_RESPONSE = """<?xml version='1.0' encoding='UTF-8'?>
<s:Envelope
  xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
  s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
  <s:Fault>
    <faultcode>SOAP-ENV:Server</faultcode>
    <faultstring>Unknown Action</faultstring>
    <detail>The requested SOAP action is not handled by pyWeMo</detail>
  </s:Fault>
</s:Body>
</s:Envelope>"""


class Subscription:
    """Subscription to a single UPnP service endpoint."""

    scheduler_event: sched.Event | None = None
    """Scheduler Event used to periodically maintain the subscription."""

    scheduler_active: bool = True
    """
    Controls whether or not the subscription will continue to be periodically
    scheduled by the Scheduler. Set to False when the device is unregistered.
    """

    expiration_time: float = 0.0
    """Time that the subscription will expire, or 0.0 if not subscribed.
    time.time() value.
    """

    event_received: bool = False
    """Has a notification event been received for this subscription?"""

    subscription_id: str | None = None
    """Subscription Identifier (SID) used to maintain/refresh the subscription.
    `None` when the subscription is not active.
    """

    default_timeout_seconds: int = 300
    """
    Request that the device keep the subscription active for this number of
    seconds.
    """

    device: Device
    """WeMo device instance."""

    callback_port: int
    """HTTP port used by devices to send event notifications."""

    service_name: str
    """Name of the subscription endpoint service."""

    path: str
    """Unique path used to for the subscription callback."""

    def __init__(
        self, device: Device, callback_port: int, service_name: str
    ) -> None:
        """Initialize a new subscription."""
        self.device = device
        self.callback_port = callback_port
        self.service_name = service_name
        self.path = f"/sub/{service_name}/{secrets.token_urlsafe(24)}"

    def __repr__(self) -> str:
        """Return a string representation of the Subscription."""
        return f'<Subscription {self.service_name} "{self.device.name}">'

    def maintain(self) -> int:
        """Start/renew the UPnP subscription.

        Returns:
            The duration of the subscription in seconds.

        Raises:
            requests.RequestException on error.
        """
        try:
            response = self._subscribe()
            if response.status_code == 412:  # Precondition Failed.
                # Invalid parameters were used for the subscription request.
                # This typically happens when the subscription_id becomes
                # invalid. Send an UNSUBSCRIBE for safety and then attempt to
                # subscribe again.
                self._unsubscribe()

                # Also reset the `event_received` boolean at this point. A 412
                # response code to a subscription renewal also happens when a
                # device restarts. When a device loses power and power is
                # restored it's possible that the device's state has changed.
                # We need to reconfirm that the initial event is received again
                # so that the device state is reported properly. And for
                # devices that don't report their initial state, it's important
                # that clients are aware that they should begin polling the
                # device again.
                self.event_received = False

                # Try the subscription again.
                response = self._subscribe()
            response.raise_for_status()
        except requests.RequestException:
            self._reset_subscription()
            raise

        return self._update_subscription(response.headers)

    def cancel(self) -> None:
        """Cancel a subscription."""
        if self.expiration_time > time.time():
            try:
                self._unsubscribe()
            except requests.RequestException:
                pass

        self._reset_subscription()

    def _subscribe(self) -> requests.Response:
        """Start/renew a subscription with a UPnP SUBSCRIBE request.

        Will renew an existing subscription if one exists, otherwise a new
        subscription will be created.
        """
        if self.subscription_id:  # Renew existing subscription.
            headers = {"SID": self.subscription_id}
        else:  # Start a new subscription.
            callback_address = get_callback_address(
                host=self.device.host,
                port=self.callback_port,
            )

            callback = f"<http://{callback_address}{self.path}>"
            headers = {"CALLBACK": callback, "NT": "upnp:event"}
        headers["TIMEOUT"] = f"Second-{self.default_timeout_seconds}"
        return requests.request(
            method="SUBSCRIBE",
            url=self.url,
            headers=headers,
            timeout=REQUESTS_TIMEOUT,
        )

    def _unsubscribe(self) -> None:
        """Remove the subscription on the WeMo with a UPnP UNSUBSCRIBE request.

        Only sends the UNSUBSCRIBE message if there is an existing
        subscription. Does nothing if there is no subscription.
        """
        if self.subscription_id:
            requests.request(
                method="UNSUBSCRIBE",
                url=self.url,
                headers={"SID": self.subscription_id},
                timeout=REQUESTS_TIMEOUT,
            )
            self.subscription_id = None

    def _update_subscription(self, headers: MutableMapping[str, str]) -> int:
        """Update UPnP subscription parameters from SUBSCRIBE response headers.

        Returns:
            The duration of the subscription in seconds.
        """
        self.subscription_id = headers.get("SID", self.subscription_id)
        if timeout_header := headers.get("TIMEOUT", None):
            timeout = min(
                int(timeout_header.replace("Second-", "")),
                self.default_timeout_seconds,
            )
        else:
            timeout = self.default_timeout_seconds
        self.expiration_time = timeout + time.time()
        return timeout

    def _reset_subscription(self) -> None:
        """Mark a subscription as no longer active.

        `self.is_subscribed` will return False after this call.
        """
        self.event_received = False
        self.expiration_time = 0.0

    @property
    def url(self) -> str:
        """URL for the UPnP subscription endoint."""
        return self.device.services[self.service_name].eventSubURL

    @property
    def is_subscribed(self) -> bool:
        """Return True if the subscription is active, False otherwise.

        Verifies that the subscription is within its expected lifetime and that
        at least one event notification has been received.

        There will always be at least one event notification because the UPnP
        spec states that the device will send an event notification when the
        subscription is first accepted.
        """
        return self.event_received and self.expiration_time > time.time()


class HTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with an 'outer' attribute."""

    outer: SubscriptionRegistry


def _start_server(port: int | None) -> HTTPServer:
    """Find a valid open port and start the HTTP server."""
    requested_port = port or os.getenv("PYWEMO_HTTP_SERVER_PORT")
    if requested_port is not None:
        start_port = int(requested_port)
        ports_to_check = 1
    else:
        start_port = 8989
        ports_to_check = 128

    for offset in range(0, ports_to_check):
        port = start_port + offset
        try:
            return HTTPServer(("", port), RequestHandler)
        except OSError as error:
            last_error = error
    raise last_error


def _cancel_events(
    scheduler: sched.scheduler, subscriptions: Iterable[Subscription]
) -> None:
    """Cancel pending scheduler events."""
    for subscription in subscriptions:
        try:
            if subscription.scheduler_event is not None:
                scheduler.cancel(subscription.scheduler_event)
        except ValueError:
            # event might execute and be removed from queue
            # concurrently.  Safe to ignore
            pass
        if subscription.scheduler_active:
            scheduler.enter(0, 0, subscription.cancel)
        # Prevent the subscription from being scheduled again.
        subscription.scheduler_active = False
        subscription.scheduler_event = None


class RequestHandler(BaseHTTPRequestHandler):
    """Handles subscription responses and long press actions from devices.

    Subscription responses:
      Pywemo can subscribe to Wemo devices. When subscribed, the Wemo device
      will send notifications when the state of the device changes. The
      do_NOTIFY method below is called when a Wemo device changes state.

    Long press actions:
      Wemo devices can control the state of other Wemo devices based on the
      rules configured for the device. A long press rule is activated whenever
      the button on the Wemo device is pressed for 2 seconds. The long press
      rule is meant to be used to control the state of another device (turn
      on/off/toggle). However for pywemo's use, a long press rule can be used
      to trigger an event notification. This is implemented by configuring the
      Wemo device to "control the state" of a virtual Wemo device. The virtual
      device is implemented by this class.

      The do_GET/do_POST/do_SUBSCRIBE methods below implement a virtual Wemo
      device. The virtual device receives requests to change its state from
      other Wemo devices on the network. When a Wemo device is configured to
      change the state of the virtual device via a long press rule the
      following sequence occurs:

      1. The Wemo device will attempt to locate the virtual device on the
      network. This is handled by the pywemo.ssdp.DiscoveryResponder class. See
      the documentation there for more information about this step.

      2. The Wemo device will fetch /setup.xml from do_GET to learn of the
      virtual device details.

      3. The Wemo device will subscribe to BinaryState notifications from the
      virtual device. The virtual device does not send any BinaryState
      notifications, but this step seems to be necessary before the next step
      can happen. This step is implemented by the do_SUBSCRIBE method.

      4. When a person presses the button on the Wemo for 2 seconds a long
      press rule is triggered. If the long press rule is configured with an
      action for the virtual device, the Wemo device will then call the do_POST
      method to update the BinaryState of the virtual device. This doesn't
      actually update any state, rather the virtual device then delivers the
      event notification to any event listeners configured to receive events
      from the pywemo SubscriptionRegistry. The event type for a long press
      action is EVENT_TYPE_LONG_PRESS.
    """

    timeout = 10
    """Do not wait for more than 10 seconds for any request to complete."""

    server: HTTPServer
    server_version = f"{BaseHTTPRequestHandler.server_version} UPnP/1.0"

    def do_NOTIFY(self) -> None:  # pylint: disable=invalid-name
        """Handle subscription responses received from devices."""
        sender_ip, _ = self.client_address
        outer = self.server.outer
        # Security consideration: Given that the subscription paths are
        # randomized, I considered removing the host/IP check below. However,
        # since these requests are not encrypted, it is possible for someone
        # to observe the random URL path. I therefore have kept the host/IP
        # check as a defense-in-depth strategy for preventing the device state
        # from being changed by someone who could observe the http requests.
        if (
            # pylint: disable=protected-access
            subscription := outer._subscription_paths.get(self.path)
        ) is None or subscription.device.host != sender_ip:
            LOG.warning(
                "Received %s event for unregistered device %s",
                self.path,
                sender_ip,
            )
        else:
            doc = self._get_xml_from_http_body()
            for propnode in doc.findall(f"./{NS}property"):
                for property_ in list(propnode):
                    outer.event(
                        subscription.device,
                        property_.tag,
                        property_.text or "",
                        path=self.path,
                    )

        self._send_response(200, RESPONSE_SUCCESS)

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Handle GET requests for a Virtual WeMo device."""
        if self.path.endswith("/setup.xml"):
            self._send_response(
                200, VIRTUAL_SETUP_XML, content_type="text/xml"
            )
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Handle POST requests for a Virtual WeMo device."""
        if self.path.endswith("/upnp/control/basicevent1"):
            sender_ip, _ = self.client_address
            outer = self.server.outer
            for (
                device
            ) in outer._subscriptions:  # pylint: disable=protected-access
                if device.host != sender_ip:
                    continue
                doc = self._get_xml_from_http_body()
                if binary_state := doc.findtext(".//BinaryState"):
                    outer.event(device, EVENT_TYPE_LONG_PRESS, binary_state)
                break
            else:
                LOG.warning(
                    "Received event for unregistered device %s", sender_ip
                )
            action = self.headers.get("SOAPACTION", "")
            response = SOAP_ACTION_RESPONSE.get(
                action, ERROR_SOAP_ACTION_RESPONSE
            )
            self._send_response(
                200, response, content_type='text/xml; charset="utf-8"'
            )
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def do_SUBSCRIBE(self) -> None:  # pylint: disable=invalid-name
        """Handle SUBSCRIBE requests for a Virtual WeMo device."""
        if self.path.endswith("/upnp/event/basicevent1"):
            self.send_response(200)
            self.send_header("CONTENT-LENGTH", "0")
            self.send_header("TIMEOUT", "Second-1801")
            # Using a randomly generated valid UUID (uuid.uuid4()).
            self.send_header(
                "SID", "uuid:a74b23d5-34b9-4f71-9f87-bed24353f304"
            )
            self.send_header("Connection", "close")
            self.end_headers()
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def do_UNSUBSCRIBE(self) -> None:  # pylint: disable=invalid-name
        """Handle UNSUBSCRIBE requests for a Virtual WeMo device."""
        if self.path.endswith("/upnp/event/basicevent1"):
            self.send_response(200)
            self.send_header("CONTENT-LENGTH", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def _send_response(
        self, code: int, body: str, *, content_type: str = "text/html"
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body.encode("UTF-8"))

    def _get_xml_from_http_body(self) -> et._Element:
        """Build the element tree root from the body of the http request."""
        content_len = int(self.headers.get("content-length", 0))
        data = self.rfile.read(content_len)
        # trim garbage from end, if any
        data = data.strip()
        return et.fromstring(data, parser=et.XMLParser(resolve_entities=False))

    # pylint: disable=redefined-builtin
    def log_message(self, format: str, *args: Any) -> None:
        """Disable error logging."""
        return


SubscriberCallback = Callable[[Device, str, str], Any]


class SubscriptionRegistry:  # pylint: disable=too-many-instance-attributes
    """Holds device subscriptions and callbacks for wemo events."""

    subscription_service_names: Iterable[str] = (
        "basicevent",
        "bridge",
        "insight",
    )
    """Potential service endpoints for subscriptions.
    A Subscription will be created for each entry as long as the service is
    supported by the device.
    """

    def __init__(self, requested_port: int | None = None) -> None:
        """Create the subscription registry object."""
        self._callbacks: dict[
            Device, list[tuple[str | None, SubscriberCallback]]
        ] = collections.defaultdict(list)
        self._exiting = False

        self._event_thread: threading.Thread | None = None
        self._event_thread_cond = threading.Condition()
        self._subscriptions: dict[Device, list[Subscription]] = {}
        self._subscription_paths: dict[str, Subscription] = {}

        def sleep(secs: float) -> None:
            with self._event_thread_cond:
                self._event_thread_cond.wait(secs)

        self._sched = sched.scheduler(time.time, sleep)

        self._http_thread: threading.Thread | None = None
        self._httpd: HTTPServer | None = None
        self._requested_port: int | None = requested_port

    @property
    def port(self) -> int:
        """Return the port that the http server is listening on."""
        assert self._httpd
        return self._httpd.server_address[1]

    def register(self, device: Device) -> None:
        """Register a device for subscription updates."""
        if not device:
            LOG.error("Called with an invalid device: %r", device)
            return

        LOG.info("Subscribing to events from %r", device)
        with self._event_thread_cond:
            subscriptions = self._subscriptions[device] = []
            for service in self.subscription_service_names:
                if service in device.services:
                    subscription = Subscription(device, self.port, service)
                    subscriptions.append(subscription)
                    self._subscription_paths[subscription.path] = subscription
                    self._schedule(0, subscription)
            self._event_thread_cond.notify()

    def unregister(self, device: Device) -> None:
        """Unregister a device from subscription updates."""
        if not device:
            LOG.error("Called with an invalid device: %r", device)
            return

        LOG.info("Unsubscribing to events from %r", device)

        with self._event_thread_cond:
            # Remove any events, callbacks, and the device itself
            self._callbacks.pop(device, None)
            subscriptions = self._subscriptions.pop(device, [])
            _cancel_events(self._sched, subscriptions)
            for subscription in subscriptions:
                del self._subscription_paths[subscription.path]
            self._event_thread_cond.notify()

    def _resubscribe(self, subscription: Subscription, retry: int = 0) -> None:
        LOG.info("Resubscribe for %r", subscription)
        try:
            timeout = subscription.maintain()
            with self._event_thread_cond:
                self._schedule(int(timeout * 0.75), subscription)
        except requests.RequestException as exc:
            LOG.warning(
                "Resubscribe error for %r (%s), will retry in %ss",
                subscription,
                exc,
                SUBSCRIPTION_RETRY,
            )
            retry += 1
            if retry > 1:
                # If this wasn't a one-off, try rediscovery
                # in case the device has changed.
                subscription.device.reconnect_with_device()
            with self._event_thread_cond:
                self._schedule(SUBSCRIPTION_RETRY, subscription, retry=retry)

    def _schedule(
        self, delay: int, subscription: Subscription, **kwargs: Any
    ) -> None:
        """Schedule a subscription.

        It is expected that the caller will hold the `_event_thread_cond` lock
        before calling this method.

        This method will not schedule a subscription when the
        `subscription.scheduler_active` property is False. This is done to
        avoid a race condition with the `unregister` method. Once `unregister`
        removes the subscription, it should not be scheduled again.
        """
        if subscription.scheduler_active:
            subscription.scheduler_event = self._sched.enter(
                delay,
                0,  # priority
                self._resubscribe,
                argument=(subscription,),
                kwargs=kwargs,
            )

    def event(
        self, device: Device, type_: str, value: str, path: str | None = None
    ) -> None:
        """Execute the callback for a received event."""
        LOG.debug(
            "Received %s event from %s(%s) - %s %s",
            path or "an",
            device,
            device.host,
            type_,
            value,
        )
        if path:
            # Update the event_received property for the subscription.
            if (
                subscription := self._subscription_paths.get(path)
            ) is not None:
                subscription.event_received = True
            else:
                LOG.warning(
                    "Received unexpected subscription path (%s) for device %s",
                    path,
                    device,
                )
        for type_filter, callback in self._callbacks.get(device, ()):
            if type_filter is None or type_ == type_filter:
                callback(device, type_, value)

    def on(  # pylint: disable=invalid-name
        self,
        device: Device,
        type_filter: str | None,
        callback: SubscriberCallback,
    ) -> None:
        """Add an event callback for a device."""
        self._callbacks[device].append((type_filter, callback))

    def is_subscribed(self, device: Device) -> bool:
        """Return True if all of the device's subscriptions are active."""
        if isinstance(device, Insight) and device.get_state() == 0:
            # Special case: When the Insight device is off, it stops reporting
            # Insight subscription updates. This causes problems for the
            # "today" energy properties on the device, which should reset at
            # midnight but don't because subscription updates have stopped.
            return False
        if isinstance(device, DimmerV2) and device.get_state() == 1:
            # Special case: The V2 (RTOS) Dimmers do not send subscription
            # updates for brightness changes. Return False so clients know
            # polling is required to update the device brightness.
            return False
        subscriptions = self._subscriptions.get(device, [])
        return len(subscriptions) > 0 and all(
            s.is_subscribed for s in subscriptions
        )

    def start(self) -> None:
        """Start the subscription registry."""
        self._httpd = _start_server(self._requested_port)
        if self._httpd is None:
            raise SubscriptionRegistryFailed(
                "Unable to bind a port for listening"
            )
        self._http_thread = threading.Thread(
            target=self._run_http_server, name="Wemo HTTP Thread"
        )
        self._http_thread.daemon = True
        self._http_thread.start()

        self._event_thread = threading.Thread(
            target=self._run_event_loop, name="Wemo Events Thread"
        )
        self._event_thread.daemon = True
        self._event_thread.start()

    def stop(self) -> None:
        """Shutdown the HTTP server."""
        assert self._httpd
        self._httpd.shutdown()

        with self._event_thread_cond:
            self._exiting = True

            # Remove any pending events
            for device_subscriptions in self._subscriptions.values():
                _cancel_events(self._sched, device_subscriptions)

            # Wake up event thread if its sleeping
            self._event_thread_cond.notify()
        self.join()
        LOG.info("Terminated threads")

    def join(self) -> None:
        """Block until the HTTP server and event threads have terminated."""
        assert self._http_thread and self._event_thread
        self._http_thread.join()
        self._event_thread.join()

    def _run_http_server(self) -> None:
        """Start the HTTP server."""
        assert self._httpd
        self._httpd.allow_reuse_address = True
        self._httpd.outer = self
        LOG.info("Listening on port %d", self.port)
        self._httpd.serve_forever()
        self._httpd.server_close()

    def _run_event_loop(self) -> None:
        """Run the event thread loop."""
        while not self._exiting:
            with self._event_thread_cond:
                while not self._exiting and self._sched.empty():
                    self._event_thread_cond.wait(10)
            self._sched.run()

    @property
    def devices(self) -> dict[str, Device]:
        """Deprecated mapping of IP address to device."""
        warnings.warn(
            "The devices dict is deprecated "
            "and will be removed in a future release.",
            DeprecationWarning,
            stacklevel=1,
        )
        return {device.host: device for device in self._subscriptions}
