"""Base WeMo Device class."""

from __future__ import annotations

import base64
import logging
import subprocess
import threading
import time
from typing import Any, Sequence

import requests

from ..exceptions import (
    ActionException,
    APNotFound,
    InvalidSchemaError,
    ResetException,
    SetupException,
    ShortPassword,
    UnknownService,
)
from ..util import MetaInfo
from .api.long_press import LongPressMixin
from .api.service import (
    REQUESTS_TIMEOUT,
    RequiredService,
    RequiredServicesMixin,
    Service,
    Session,
)
from .api.wemo_services import WeMoServiceTypesMixin
from .api.xsd_types import DeviceDescription

LOG = logging.getLogger(__name__)

# Start with the most commonly used port
PROBE_PORTS = (49153, 49152, 49154, 49151, 49155, 49156, 49157, 49158, 49159)


def probe_wemo(
    host: str,
    ports: Sequence[int] = PROBE_PORTS,
    probe_timeout: float = REQUESTS_TIMEOUT,
    match_udn: str | None = None,
) -> int | None:
    """Probe a host for the current port.

    This probes a host for known-to-be-possible ports and
    returns the one currently in use. If no port is discovered
    then it returns None.
    """
    for port in ports:
        try:
            response = requests.get(
                f"http://{host}:{port}/setup.xml", timeout=probe_timeout
            )
            try:
                device = DeviceDescription.from_xml(response.content)
            except InvalidSchemaError:
                continue
            if match_udn and match_udn != device.udn:
                LOG.error(
                    "Reconnected to a different WeMo. "
                    "Expected %s / Received %s",
                    match_udn,
                    device.udn,
                )
                continue
            return port
        except requests.exceptions.ConnectTimeout:
            # If we timed out connecting, then the wemo is gone,
            # no point in trying further.
            LOG.debug(
                "Timed out connecting to %s on port %i, wemo is offline",
                host,
                port,
            )
            break
        except requests.exceptions.Timeout:
            # Apparently sometimes wemos get into a wedged state where
            # they still accept connections on an old port, but do not
            # respond. If that happens, we should keep searching.
            LOG.debug("No response from %s on port %i, continuing", host, port)
            continue
        except requests.exceptions.ConnectionError:
            pass
    return None


def probe_device(device: Device) -> int | None:
    """Probe a device for available port.

    This is an extension for probe_wemo, also probing current port.
    """
    ports = list(PROBE_PORTS)
    if device.port in ports:
        ports.remove(device.port)
    ports.insert(0, device.port)

    return probe_wemo(device.host, ports, match_udn=device.udn)


class Device(DeviceDescription, RequiredServicesMixin, WeMoServiceTypesMixin):
    """Base object for WeMo devices."""

    EVENT_TYPE_BINARY_STATE = "BinaryState"

    def __init__(self, url: str) -> None:
        """Create a WeMo device."""
        self._state: int | None = None
        self.basic_state_params: dict[str, str] = {}
        self._reconnect_lock = threading.Lock()
        self.session = Session(url)
        xml = self.session.get(url).data

        try:
            super().__init__(**DeviceDescription.dict_from_xml(xml))
        except InvalidSchemaError:
            LOG.debug("Received invalid schema from %s: %r", url, xml)
            raise

        self.services = {}
        for svc in self._services:
            service = Service(self, svc)
            self.services[service.name] = service
            setattr(self, service.name, service)
        self._check_required_services(self.services.values())

    @property
    def _required_services(self) -> list[RequiredService]:
        return super()._required_services + [
            RequiredService(name="basicevent", actions=["GetBinaryState"])
        ]

    def _reconnect_with_device_by_discovery(self) -> None:
        """
        Scan network to find the device again.

        Wemos tend to change their port number from time to time.
        Whenever requests throws an error, we will try to find the device again
        on the network and update this device.
        """
        # Put here to avoid circular dependency
        # pylint: disable=import-outside-toplevel
        from ..ssdp import scan

        LOG.info("Trying to reconnect with %s", self.name)

        found = scan(max_entries=1, match_udn=self.udn)
        if found and found[0].location:
            LOG.info("Found %s again, updating location", self.name)
            self.session.url = found[0].location
        else:
            LOG.error("Unable to reconnect with %s", self.name)

    def _reconnect_with_device_by_probing(self) -> bool:
        """Attempt to reconnect to the device on the existing port."""
        port = probe_device(self)

        if port is None:
            LOG.error("Unable to re-probe wemo %s at %s", self, self.host)
            return False

        LOG.info("Reconnected to wemo %s on port %i", self, port)
        self.session.url = f"http://{self.host}:{port}/setup.xml"
        return True

    def reconnect_with_device(self) -> None:
        """Re-probe & scan network to rediscover a disconnected device."""
        # Avoid retrying from multiple threads
        # pylint: disable=consider-using-with
        if not self._reconnect_lock.acquire(blocking=False):
            return
        try:
            if not self._reconnect_with_device_by_probing():
                self._reconnect_with_device_by_discovery()
        finally:
            self._reconnect_lock.release()

    @staticmethod
    def parse_basic_state(params: str) -> dict[str, str]:
        """Parse the basic state response from the device."""
        # The BinaryState `params` could have two different formats:
        #   1|1492338954|0|922|14195|1209600|0|940670|15213709|227088884
        #   1
        # In both formats, the first integer value indicates the state.
        # 0 if off, 1 if on,
        return {"state": params.split("|")[0]}

    def update_binary_state(self) -> None:
        """Update the cached copy of the basic state response."""
        self.basic_state_params = self.basicevent.GetBinaryState() or {}

    def subscription_update(self, _type: str, _params: str) -> bool:
        """Update device state based on subscription event."""
        LOG.debug("subscription_update %s %s", _type, _params)
        if _type == self.EVENT_TYPE_BINARY_STATE:
            try:
                self._state = int(
                    self.parse_basic_state(_params).get("state", "0")
                )
            except ValueError:
                LOG.error(
                    "Unexpected BinaryState value `%s` for device %s.",
                    _params,
                    self.name,
                )
            return True
        return False

    def get_state(self, force_update: bool = False) -> int:
        """Return 0 if off and 1 if on."""
        if force_update or self._state is None:
            self.update_binary_state()

            try:
                self._state = int(
                    self.basic_state_params.get("BinaryState", 0)
                )
            except ValueError:
                self._state = 0

        return self._state

    def get_service(self, name: str) -> Service:
        """Get service object by name."""
        try:
            return self.services[name]
        except KeyError as exc:
            raise UnknownService(name) from exc

    def list_services(self) -> list[str]:
        """Return list of services."""
        return list(self.services.keys())

    def explain(self) -> None:
        """Print information about the device and its actions."""
        for name, svc in self.services.items():
            print(name)
            print("-" * len(name))
            for aname, action in svc.actions.items():
                inputs = ", ".join(str(val) for val in action.args)
                outputs = ", ".join(str(val) for val in action.returns)
                if len(action.returns) > 1:
                    outputs = "(" + outputs + ")"
                if outputs:
                    outputs = " -> " + outputs
                print(f"  {aname}({inputs}){outputs}")
            print()

    def reset(self, data: bool, wifi: bool) -> str:
        """Reset Wemo device.

        Args:
          data (bool):
            Set to True to reset the data ("Clear Personalized Info" in the
            Wemo app), which resets the device name and cleans the icon and
            rules.
          wifi (bool):
            Set to True to clear wifi information ("Change Wi-Fi" in the Wemo
            app), which does not clear the rules, name, etc.

        Notes
        -----
        Setting both to true is equivalent to a "Factory Restore" from the app.

        Wemo devices contain a hardware reset procedure as well, so this
        method is mainly for convenience or when physical access is not
        possible.

        From testing on a handful of devices, the Reset codes used in the
        ReSetup action below were consistent.  These could potentially change
        in a future firmware revision or may be different for other untested
        devices.
        """
        try:
            action = self.basicevent.ReSetup
        except AttributeError as exc:
            raise ResetException(
                "Cannot reset device: ReSetup action not found"
            ) from exc

        if data and wifi:
            LOG.info("Clearing data and wifi (factory reset)")
            result = action(Reset=2)
        elif data:
            LOG.info("Clearing data (icon, rules, etc)")
            result = action(Reset=1)
        elif wifi:
            LOG.info("Clearing wifi information")
            result = action(Reset=5)
        else:
            raise ResetException("no action requested")

        try:
            status = result["Reset"].strip().lower()
        except KeyError:
            status = "unknown"

        if status == "success":
            LOG.info("reset successful")
        else:
            # one test unit always returns "reset_remote" here instead of
            # "success", but it appears to still reset successfully
            LOG.warning("result of reset (may be successful): %s", status)

        return status

    def factory_reset(self) -> str:
        """Perform a full factory reset (convenience method)."""
        return self.reset(data=True, wifi=True)

    @staticmethod
    def encrypt_aes128(
        password: str, wemo_metadata: str, is_rtos: bool
    ) -> str:
        """Encrypt a password using OpenSSL.

        Function borrows heavily from Vadim Kantorov's "wemosetup" script:
        https://github.com/vadimkantorov/wemosetup
        """
        if not password:
            raise SetupException("password required for AES")

        # Wemo uses some meta information for salt and iv
        meta_info = MetaInfo.from_meta_info(wemo_metadata)
        keydata = (
            meta_info.mac[:6] + meta_info.serial_number + meta_info.mac[6:12]
        )
        if is_rtos:
            keydata += "b3{8t;80dIN{ra83eC1s?M70?683@2Yf"

        salt, initialization_vector = keydata[:8], keydata[:16]
        if len(salt) != 8 or len(initialization_vector) != 16:
            LOG.warning("device meta information may not be supported")

        # call OpenSSL to encrypt the data
        try:
            openssl = subprocess.run(
                [
                    "openssl",
                    "enc",
                    "-aes-128-cbc",
                    "-md",
                    "md5",
                    "-S",
                    salt.encode("utf-8").hex(),
                    "-iv",
                    initialization_vector.encode("utf-8").hex(),
                    "-pass",
                    "pass:" + keydata,
                ],
                check=True,
                capture_output=True,
                input=password.encode("utf-8"),
            )
        except FileNotFoundError as exc:
            raise SetupException(
                "openssl command failed (openssl not installed / not on path?)"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise SetupException("openssl command failed") from exc

        output = openssl.stdout
        if output.startswith(b"Salted__"):
            # remove 16byte magic and salt prefix inserted by OpenSSL, which
            # is of the form "Salted__XXXXXXXX" before the actual password
            output = output[16:]
        encrypted_password = base64.b64encode(output).decode()

        # the last 4 digits that wemo expects is xxyy, where:
        #     xx: length of the encrypted password as hexadecimal
        #     yy: length of the original password as hexadecimal
        len_encrypted = len(encrypted_password)
        len_original = len(password)
        LOG.debug("password length (before encryption): %s", len_original)
        LOG.debug("password length (after encryption): %s", len_encrypted)
        if len_encrypted > 255 or len_original > 255:
            # untested, but over 255 characters would require >2 hex digits
            raise SetupException(
                "Wemo requires the wifi password (including after encryption) "
                "to be 255 or less characters, but found password of length "
                f"{len_original} (and {len_encrypted} after encryption)."
            )

        if not is_rtos:
            encrypted_password += f"{len_encrypted:#04x}"[2:]
            encrypted_password += f"{len_original:#04x}"[2:]
        return encrypted_password

    def setup(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
        """Connect Wemo to wifi network.

        This function should be used and will capture several potential
        exceptions to indicate when the setup method won't work on a device.

        Args:
          ssid (str):
            SSID to connect the device to.
          password (str):
            Password for the indicated SSID.  This password will be encrypted
            with OpenSSL and then sent to the device.  To connect to an open,
            unsecured network, pass anything for the password as it will be
            ignored.
          timeout (float, optional):
            Number of seconds to wait and poll a device to see if it has
            successfully connected to the network.  The minimum value allows is
            15 seconds as devices sometimes take 10-15 seconds to connect.
          connection_attempts (int, optional):
            Number of times to try connecting a debice to the network, if it
            has failed to connect within `timeout` seconds.
          status_delay (float, optional):
            Number of seconds to delay between each called to the connection
            status of the device.  Generally should prefer this to be as short
            as possible, but not too quick to overload the device with
            requests.  It must be less than or equal to half of the `timeout`.

        Notes
        -----
        The timeout applies to each connection attempt, so the total wait time
        will be approximately `timeout * connection_attempts`.
        """
        try:
            return self._setup(*args, **kwargs)
        except (UnknownService, AttributeError, KeyError) as exc:
            #    Exception       | Reason to catch it
            #    --------------------------------------------------------------
            #    UnknownService  | some devices or firmwares may not have the
            #                    | services used
            #    --------------------------------------------------------------
            #    AttributeError  | some devices or firmwares may not have the
            #                    | actions used
            #    --------------------------------------------------------------
            #    KeyError        | an expected result (return from an action)
            #                    | does not exist (e.g. ApList)
            #    --------------------------------------------------------------
            raise SetupException(f"pywemo cannot setup {self}") from exc
        except ActionException as exc:
            #    Exception       | Reason to catch it
            #    --------------------------------------------------------------
            #    ActionException | one of the action calls never returned!  The
            #                    | device was not re-discovered.  It may have
            #                    | lost power (been unplugged).
            #    --------------------------------------------------------------
            raise SetupException(
                f"pywemo lost device {self} and was unable to reconnect.  "
                "Setup status is uncertain, re-probing and checking is "
                "required."
            ) from exc

    def _setup(  # noqa: C901
        # pylint: disable=too-many-arguments,too-many-branches,too-many-locals
        # pylint: disable=too-many-statements
        self,
        ssid: str,
        password: str,
        timeout: float = 20.0,
        connection_attempts: int = 1,
        status_delay: float = 1.0,
    ) -> tuple[str, str]:
        """Connect Wemo to wifi network.

        See the setup method for details.
        """
        # a timeout of less than 20 is too short for many devices, so require
        # at least 20 seconds.
        timeout = max(timeout, 20.0)
        status_delay = min(status_delay, timeout / 2.0)
        connection_attempts = int(max(1, connection_attempts))

        # find all access points that the device can see, and select the one
        # matching the desired SSID
        LOG.info("scanning for AP's...")
        wifisetup = self.get_service("WiFiSetup")
        access_points = wifisetup.GetApList()["ApList"]

        selected_ap = None
        for access_point in access_points.split("\n")[1:]:
            access_point = access_point.strip().rstrip(",")
            if not access_point.strip() or "|" not in access_point:
                continue
            LOG.debug("found AP: %s", access_point)
            if access_point.startswith(f"{ssid}|"):
                selected_ap = access_point
                LOG.info("selecting AP: %s", selected_ap)
                break

        if selected_ap is None:
            raise APNotFound(f"AP with SSID {ssid} not found.  Try again.")

        # get some information about the access point
        columns = selected_ap.split("|")
        channel = columns[1].strip()
        auth_mode, encryption_method = columns[-1].strip().split("/")
        LOG.debug("AP channel: %s", channel)
        LOG.debug("AP authorization mode(s): %s", auth_mode)
        LOG.debug("AP encryption method: %s", encryption_method)

        # check if the encryption type is supported by this script
        supported_encryptions = {"NONE", "AES"}
        if encryption_method not in supported_encryptions:
            raise SetupException(
                f"Encryption {encryption_method} not currently supported.  "
                f'Supported encryptions are: {",".join(supported_encryptions)}'
            )

        # try to connect the device to the selected network
        if encryption_method == "NONE":
            LOG.debug("selected network has no encryption (password ignored)")
            auth_mode = "OPEN"
            encrypted_password = ""
        else:
            # get the meta information of the device and encrypt the password
            meta_info = self.get_service("metainfo").GetMetaInfo()["MetaInfo"]
            is_rtos = self._config_any.get("rtos", "0") == "1"
            encrypted_password = self.encrypt_aes128(
                password, meta_info, is_rtos
            )

        # optionally make multiple connection attempts
        start_time = time.time()

        # status messages:
        #     0: still trying to connect to network
        #     1: successfully connected
        #     2: short password (Wemo requires at least 8 characters)
        #     3: performing handshake? (uncertain, but devices generally
        #        go to status 3 for a few moments before switching to
        #        successful status 1)
        skip = {"1", "2"}

        for attempt in range(connection_attempts):
            LOG.info("sending connection request (try %s)", attempt + 1)
            # success rate is much higher if the ConnectHomeNetwork command is
            # sent twice (not sure why!)
            for retry in range(2):
                result = wifisetup.ConnectHomeNetwork(
                    ssid=ssid,
                    auth=auth_mode,
                    password=encrypted_password,
                    encrypt=encryption_method,
                    channel=channel,
                )
                try:
                    status = result["PairingStatus"]
                except KeyError:
                    # print entire dictionary if PairingStatus doesn't exist
                    status = repr(result)
                LOG.debug("pairing status (send %s): %s", retry + 1, status)
                if retry == 0:
                    # only delay on the first call
                    time.sleep(0.10)

            timeout_start = time.time()
            LOG.info("starting status checks (%s second timeout)", timeout)
            status = ""

            # Make an initial, quicker check
            time.sleep(min(0.50, status_delay / 3.0))
            status = wifisetup.GetNetworkStatus()["NetworkStatus"]
            LOG.debug("initial status check: %s", status)

            while time.time() - timeout_start < timeout and status not in skip:
                time.sleep(status_delay)
                status = wifisetup.GetNetworkStatus()["NetworkStatus"]
                LOG.debug(
                    "network status after %.2f seconds: %s",
                    time.time() - timeout_start,
                    status,
                )
            if status in skip:
                # skip any further attempts
                break

        # status 3 usually (always?) occurs shortly before it switches to
        # status 1, so if the status is 3 here, then delay a few more seconds
        # to see if it switches to status 1.
        if status == "3":
            LOG.debug("delaying a little longer (status 3)...")
            loops = 3  # 3 seconds with default status_delay
            while loops > 0 and status not in skip:
                time.sleep(status_delay)
                status = wifisetup.GetNetworkStatus()["NetworkStatus"]
                loops -= 1

        try:
            result = wifisetup.CloseSetup()
        except AttributeError:
            # if CloseSetup doesn't exist, it may still work
            result = {"status": "CloseSetup action not available"}

        try:
            close_status = result["status"]
        except KeyError:
            # print entire dictionary if status doesn't exist
            close_status = repr(result)
        LOG.debug("network status: %s", status)
        LOG.debug("close status: %s", close_status)

        if status == "2":
            # we could check the password length way earlier (start of the
            # function), but perhaps Wemo will change this requirement some
            # day to make it longer, so instead just use the status '2' return
            # code.
            raise ShortPassword(
                "Password is too short (Wemo requires at least 8 characters)."
            )

        if status == "1" and close_status == "success":
            try:
                self.basicevent.SetSetupDoneStatus()
            except AttributeError:
                LOG.debug(
                    "SetSetupDoneStatus not available (some devices do not "
                    "have this method)"
                )
            LOG.info(
                'Wemo device connected to "%s" in %.2f seconds (%s connection '
                "attempts(s))",
                ssid,
                time.time() - start_time,
                attempt + 1,
            )
        elif status == "1":
            LOG.warning(
                'Wemo device likely connected to "%s", but should be verified '
                '(CloseSetup returned "%s").',
                ssid,
                close_status,
            )
        elif status == "3":
            raise SetupException(
                f'Wemo device failed to connect to "{ssid}", but has status=3,'
                "which usually precedes a successful connection.  Thus it may "
                "still connect to the network shortly.  Otherwise, please try "
                "again."
            )
        else:
            raise SetupException(
                f'Wemo device failed to connect to "{ssid}".  It could be a '
                "wrong password or Wemo device/firmware issue.  Please try "
                "again."
            )

        return status, close_status

    @classmethod
    def supports_long_press(cls) -> bool:
        """Return True of the device supports long press events."""
        return issubclass(cls, LongPressMixin)

    @property
    def host(self) -> str:
        """Host name of the device's UPnP web server."""
        return self.session.host

    @property
    def port(self) -> int:
        """TCP port for the device's UPnP web server."""
        return self.session.port

    @property
    def device_type(self) -> str:
        """Return what kind of WeMo this device is."""
        return type(self).__name__

    def __repr__(self) -> str:
        """Return a string representation of the device."""
        return f'<WeMo {self.device_type} "{self.name}">'


class UnsupportedDevice(Device):
    """Representation of a WeMo device without a definition in pywemo.

    This class is used if an apparent WeMo device is found on the network via
    upnp discovery, but the device does not yet exist in pywemo.  This will
    allow a user to see that something is discovered and manually interact with
    it as well as aide in creating a permenant class for the new product.
    """
