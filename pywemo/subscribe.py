"""Module to listen for wemo events."""
import collections
import logging
import sched
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, Iterable, Optional

import requests
from lxml import etree as et

from .ouimeaux_device import Device
from .ouimeaux_device.api.long_press import VIRTUAL_DEVICE_UDN
from .util import get_ip_address

# Subscription event types.
EVENT_TYPE_BINARY_STATE = "BinaryState"
EVENT_TYPE_INSIGHT_PARAMS = "InsightParams"
EVENT_TYPE_LONG_PRESS = "LongPress"

LOG = logging.getLogger(__name__)
NS = "{urn:schemas-upnp-org:event-1-0}"
RESPONSE_SUCCESS = '<html><body><h1>200 OK</h1></body></html>'
RESPONSE_NOT_FOUND = '<html><body><h1>404 Not Found</h1></body></html>'
SUBSCRIPTION_RETRY = 60

VIRTUAL_SETUP_XML = f"""<?xml version="1.0"?>
<root xmlns="urn:Belkin:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:Belkin:device:switch:1</deviceType>
    <friendlyName>pywemo virtual device</friendlyName>
    <manufacturer>pywemo</manufacturer>
    <manufacturerURL>https://github.com/pavoni/pywemo</manufacturerURL>
    <modelDescription>pywemo virtual device</modelDescription>
    <modelName>LightSwitch</modelName>
    <modelNumber>1.0</modelNumber>
    <hwVersion>v1</hwVersion>
    <modelURL>http://www.belkin.com/plugin/</modelURL>
    <serialNumber>VirtualDevice</serialNumber>
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
</device>
</root>"""

SubscribeUrlFn = Callable[[Device], str]


class SubscriptionRegistryFailed(Exception):
    """General exceptions related to the subscription registry."""

    pass


def _start_server():
    """Find a valid open port and start the HTTP server."""
    for i in range(0, 128):
        port = 8989 + i
        try:
            return ThreadingHTTPServer(('', port), RequestHandler)
        except (OSError, socket.error):
            continue
    return None


def _basic_event_subscription_url(device: Device) -> str:
    """Return the basic event subscription URL."""
    return device.basicevent.eventSubURL


def _insight_event_subscription_url(device: Device) -> str:
    """Return the insight event subscription URL."""
    return device.insight.eventSubURL


def _cancel_events(
    scheduler: sched.scheduler, events: Iterable[sched.Event]
) -> None:
    """Cancel pending scheduler events."""
    for event in events:
        try:
            scheduler.cancel(event)
        except ValueError:
            # event might execute and be removed from queue
            # concurrently.  Safe to ignore
            pass


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

    # Do not wait for more than 10 seconds for any request to complete.
    timeout = 10

    def do_NOTIFY(self):  # pylint: disable=invalid-name
        """Handle subscription responses received from devices."""
        sender_ip, _ = self.client_address
        outer = self.server.outer
        device = outer.devices.get(sender_ip)
        if device is None:
            LOG.warning('Received event for unregistered device %s', sender_ip)
        else:
            doc = self._get_xml_from_http_body()
            for propnode in doc.findall('./{0}property'.format(NS)):
                for property_ in list(propnode):
                    text = property_.text
                    outer.event(device, property_.tag, text)

        self._send_response(200, RESPONSE_SUCCESS)

    def do_GET(self):  # pylint: disable=invalid-name
        """Handle GET requests for a Virtual WeMo device."""
        if self.path.endswith("/setup.xml"):
            self._send_response(
                200, VIRTUAL_SETUP_XML, content_type="text/xml"
            )
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def do_POST(self):  # pylint: disable=invalid-name
        """Handle POST requests for a Virtual WeMo device."""
        if self.path.endswith("/upnp/control/basicevent1"):
            sender_ip, _ = self.client_address
            outer = self.server.outer
            device = outer.devices.get(sender_ip)
            if device is None:
                LOG.warning(
                    'Received event for unregistered device %s', sender_ip
                )
            else:
                doc = self._get_xml_from_http_body()
                binary_state = doc.find('.//BinaryState')
                if binary_state is not None:
                    text = binary_state.text
                    outer.event(device, EVENT_TYPE_LONG_PRESS, text)
            self._send_response(200, RESPONSE_SUCCESS)
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def do_SUBSCRIBE(self):  # pylint: disable=invalid-name
        """Handle SUBSCRIBE requests for a Virtual WeMo device."""
        if self.path.endswith("/upnp/event/basicevent1"):
            self.send_response(200)
            self.send_header("CONTENT-LENGTH", "0")
            self.send_header("TIMEOUT", "Second-1801")
            # Using a randomly generated valid UUID (uuid.uuid4()).
            self.send_header(
                "SID", "uuid:a74b23d5-34b9-4f71-9f87-bed24353f304"
            )
            self.send_header('Connection', 'close')
            self.end_headers()
        else:
            self._send_response(404, RESPONSE_NOT_FOUND)

    def _send_response(self, code, body, *, content_type="text/html"):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', len(body))
        self.send_header('Connection', 'close')
        self.end_headers()
        if body:
            self.wfile.write(body.encode("UTF-8"))

    def _get_xml_from_http_body(self):
        """Build the element tree root from the body of the http request."""
        content_len = int(self.headers.get('content-length', 0))
        data = self.rfile.read(content_len)
        # trim garbage from end, if any
        data = data.strip()
        return et.fromstring(data)

    # pylint: disable=redefined-builtin
    def log_message(self, format, *args):
        """Disable error logging."""
        return


class SubscriptionRegistry:
    """Class for subscribing to wemo events."""

    def __init__(self):
        """Create the subscription registry object."""
        self.devices = {}
        self._callbacks = collections.defaultdict(list)
        self._exiting = False

        self._event_thread = None
        self._event_thread_cond = threading.Condition()
        self._events: Dict[str, Dict[SubscribeUrlFn, sched.Event]] = {}

        def sleep(secs):
            with self._event_thread_cond:
                self._event_thread_cond.wait(secs)

        self._sched = sched.scheduler(time.time, sleep)

        self._http_thread = None
        self._httpd = None

    @property
    def port(self) -> int:
        """Return the port that the http server is listening on."""
        return self._httpd.server_address[1]

    def register(self, device):
        """Register a device for subscription updates."""
        if not device:
            LOG.error("Called with an invalid device: %r", device)
            return

        LOG.info("Subscribing to events from %r", device)
        self.devices[device.host] = device

        with self._event_thread_cond:
            self._events[device.serialnumber] = {}
            # Basic events
            self._schedule(0, device, _basic_event_subscription_url)
            # Insight events
            if hasattr(device, 'insight'):
                self._schedule(0, device, _insight_event_subscription_url)
            self._event_thread_cond.notify()

    def unregister(self, device):
        """Unregister a device from subscription updates."""
        if not device:
            LOG.error("Called with an invalid device: %r", device)
            return

        LOG.info("Unsubscribing to events from %r", device)

        with self._event_thread_cond:
            # Remove any events, callbacks, and the device itself
            if self._callbacks[device.serialnumber] is not None:
                del self._callbacks[device.serialnumber]
            events = self._events.get(device.serialnumber, None)
            if events is not None:
                _cancel_events(self._sched, events.values())
                del self._events[device.serialnumber]
            if self.devices[device.host] is not None:
                del self.devices[device.host]

            self._event_thread_cond.notify()

    def _resubscribe(
        self,
        device: Device,
        url_fn: SubscribeUrlFn,
        sid: Optional[str] = None,  # pylint: disable=unsubscriptable-object
        retry: int = 0,
    ) -> None:
        path = url_fn(device).rsplit('/')[-1]
        LOG.info("Resubscribe for %s %s", path, device)
        headers = {'TIMEOUT': 'Second-300'}
        if sid is not None:
            headers['SID'] = sid
        else:
            host = get_ip_address(host=device.host)
            headers.update(
                {
                    "CALLBACK": '<http://%s:%d/%s>' % (host, self.port, path),
                    "NT": "upnp:event",
                }
            )
        try:
            self._url_resubscribe(device, headers, sid, url_fn)
        except requests.exceptions.RequestException as exc:
            LOG.warning(
                "Resubscribe error for %s %s (%s), will retry in %ss",
                path,
                device,
                exc,
                SUBSCRIPTION_RETRY,
            )
            retry += 1
            if retry > 1:
                # If this wasn't a one-off, try rediscovery
                # in case the device has changed.
                if device.rediscovery_enabled:
                    device.reconnect_with_device()
            with self._event_thread_cond:
                if url_fn in self._events.get(device.serialnumber, {}):
                    self._schedule(
                        SUBSCRIPTION_RETRY,
                        device,
                        url_fn,
                        sid=sid,
                        retry=retry,
                    )

    def _url_resubscribe(
        self,
        device: Device,
        headers: Dict[str, str],
        sid: Optional[str],  # pylint: disable=unsubscriptable-object
        url_fn: SubscribeUrlFn,
    ) -> None:
        request_headers = headers.copy()
        url = url_fn(device)
        response = requests.request(
            method="SUBSCRIBE", url=url, headers=request_headers, timeout=10
        )
        if response.status_code == 412 and sid:
            # Invalid subscription ID. Send an UNSUBSCRIBE for safety and
            # start over.
            requests.request(
                method='UNSUBSCRIBE', url=url, headers={'SID': sid}, timeout=10
            )
            return self._resubscribe(device, url_fn)
        timeout = int(
            response.headers.get('TIMEOUT', headers.get('TIMEOUT')).replace(
                'Second-', ''
            )
        )
        sid = response.headers.get('sid', sid)
        with self._event_thread_cond:
            if url_fn in self._events.get(device.serialnumber, {}):
                self._schedule(int(timeout * 0.75), device, url_fn, sid=sid)

    def _schedule(
        self,
        delay: int,
        device: Device,
        url_fn: SubscribeUrlFn,
        **kwargs,
    ) -> None:
        """Schedule a subscription."""
        self._events[device.serialnumber][url_fn] = self._sched.enter(
            delay,
            0,
            self._resubscribe,
            argument=(device, url_fn),
            kwargs=kwargs,
        )

    def event(self, device, type_, value):
        """Execute the callback for a received event."""
        LOG.info(
            "Received event from %s(%s) - %s %s",
            device,
            device.host,
            type_,
            value,
        )
        for type_filter, callback in self._callbacks.get(
            device.serialnumber, ()
        ):
            if type_filter is None or type_ == type_filter:
                callback(device, type_, value)

    def on(self, device, type_filter, callback):
        """Add an event callback for a device."""
        self._callbacks[device.serialnumber].append((type_filter, callback))

    def start(self):
        """Start the subscription registry."""
        self._httpd = _start_server()
        if self._httpd is None:
            raise SubscriptionRegistryFailed(
                'Unable to bind a port for listening'
            )
        self._http_thread = threading.Thread(
            target=self._run_http_server, name='Wemo HTTP Thread'
        )
        self._http_thread.deamon = True
        self._http_thread.start()

        self._event_thread = threading.Thread(
            target=self._run_event_loop, name='Wemo Events Thread'
        )
        self._event_thread.deamon = True
        self._event_thread.start()

    def stop(self):
        """Shutdown the HTTP server."""
        self._httpd.shutdown()

        with self._event_thread_cond:
            self._exiting = True

            # Remove any pending events
            for device_events in self._events.values():
                _cancel_events(self._sched, device_events.values())

            # Wake up event thread if its sleeping
            self._event_thread_cond.notify()
        self.join()
        LOG.info("Terminated threads")

    def join(self):
        """Block until the HTTP server and event threads have terminated."""
        self._http_thread.join()
        self._event_thread.join()

    def _run_http_server(self):
        """Start the HTTP server."""
        self._httpd.allow_reuse_address = True
        self._httpd.outer = self
        LOG.info("Listening on port %d", self.port)
        self._httpd.serve_forever()

    def _run_event_loop(self):
        """Run the event thread loop."""
        while not self._exiting:
            with self._event_thread_cond:
                while not self._exiting and self._sched.empty():
                    self._event_thread_cond.wait(10)
            self._sched.run()
