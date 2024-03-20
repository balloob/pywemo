"""Tests for pywemo.ouimeaux_device.api.service."""

from __future__ import annotations

import unittest.mock as mock

import pytest
import urllib3
from lxml import etree as et

import pywemo.ouimeaux_device.api.service as svc
from pywemo import WeMoDevice
from pywemo.exceptions import HTTPException, InvalidSchemaError, SOAPFault

BODY_KWARG_KEY = "body"
HEADERS_KWARG_KEY = "headers"
TIMEOUT_KWARG_KEY = "timeout"
CONTENT_TYPE_KEY = "Content-Type"
SOAPACTION_KEY = "SOAPACTION"

MOCK_ARGS_ORDERED = 0
MOCK_ARGS_KWARGS = 1

svc.LOG = mock.Mock()

original_fromstring = et.fromstring
MOCK_RESPONSE = (
    b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
    b' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    b'<s:Body>\n<u:GetInsightParamsResponse xmlns:u="urn:Belkin:service:metainfo:1">'  # noqa: E501
    b"\r\n<InsightParams>0|1604849509|85|1315|27628|1209600|772|0|21689183|386799026.000000|8000"  # noqa: E501
    b"</InsightParams>\r\n</u:GetInsightParamsResponse>\r\n</s:Body> </s:Envelope>"  # noqa: E501
)


class TestSession:
    """Test the Session class."""

    def test_init_and_properties(self):
        url = "HTTP://1.2.3.4/setup/#"
        session = svc.Session(url, retries=3, timeout=4)
        assert session.url == "http://1.2.3.4/setup/"
        assert session.host == "1.2.3.4"
        assert session.port == 80
        assert session.retries == 3
        assert session.timeout == 4

        url = "HTTP://5.6.7.8:9090/setup.xml"
        orig = session.url = url
        assert orig == url
        assert session.url == "http://5.6.7.8:9090/setup.xml"
        assert session.host == "5.6.7.8"
        assert session.port == 9090

    @mock.patch("urllib3.PoolManager.request")
    def test_404_raises(self, mock_request):
        response = mock.Mock()
        response.status = 404
        mock_request.return_value = response

        session = svc.Session("http://1.2.3.4")
        with pytest.raises(HTTPException):
            session.get("/")

    @mock.patch(
        "urllib3.PoolManager.request", side_effect=urllib3.exceptions.HTTPError
    )
    def test_urllib_raises_http_exception(self, mock_request):
        session = svc.Session("http://1.2.3.4")
        with pytest.raises(HTTPException):
            session.get("/")

    @mock.patch("urllib3.PoolManager")
    def test_arg_override(self, mock_poolmgr):
        pool = mock.Mock()
        mock_poolmgr.return_value.__enter__.return_value = pool
        response = mock.Mock()
        response.status = 200
        pool.request.return_value = response

        session = svc.Session("http://1.2.3.4")
        session.get("/", retries=3, timeout=4)
        mock_poolmgr.assert_called_once_with(retries=3, timeout=4)

        mock_poolmgr.reset_mock()
        session.post("/", retries=3, timeout=4)
        mock_poolmgr.assert_called_once_with(retries=3, timeout=4)


class TestAction:
    """Test class for actions."""

    @staticmethod
    def get_mock_action(name="action_name", service_type="", url=""):
        device = mock.create_autospec(WeMoDevice)
        device.name = "device_name"
        device.session = svc.Session("http://192.168.1.100:53892/")

        service = mock.create_autospec(svc.Service)
        service.device = device
        service.name = "ServiceName"
        service.serviceType = service_type
        service.controlURL = url

        action_config = svc.ActionProperties(name=name, arguments=[])

        return svc.Action(service, action_config)

    @pytest.fixture(autouse=True)
    def mock_et_fromstring(self):
        resp = et.fromstring(MOCK_RESPONSE)
        with mock.patch("lxml.etree.fromstring", return_value=resp) as mocked:
            yield mocked

    def test_call_post_request_is_made_exactly_once_when_successful(self):
        action = self.get_mock_action()
        action.service.device.session.post = post_mock = mock.Mock()

        action()

        assert post_mock.call_count == 1

    def test_call_request_has_well_formed_xml_body(self):
        action = self.get_mock_action(name="cool_name", service_type="service")
        action.service.device.session.post = post_mock = mock.Mock()

        action()

        body = post_mock.call_args[MOCK_ARGS_KWARGS][BODY_KWARG_KEY]
        et.fromstring(body)  # will raise error if xml is malformed

    def test_call_request_has_correct_header_keys(self):
        action = self.get_mock_action()
        action.service.device.session.post = post_mock = mock.Mock()

        action()

        headers = post_mock.call_args[MOCK_ARGS_KWARGS][HEADERS_KWARG_KEY]
        for header in [CONTENT_TYPE_KEY, SOAPACTION_KEY]:
            assert header in headers

    def test_call_headers_has_correct_content_type(self):
        action = self.get_mock_action()
        action.service.device.session.post = post_mock = mock.Mock()

        action()

        headers = post_mock.call_args[MOCK_ARGS_KWARGS][HEADERS_KWARG_KEY]
        content_type_header = headers[CONTENT_TYPE_KEY]

        assert content_type_header == "text/xml"

    def test_call_headers_has_correct_soapaction(self):
        service_type = "some_service"
        name = "cool_name"
        action = self.get_mock_action(name, service_type)
        action.service.device.session.post = post_mock = mock.Mock()

        action()

        headers = post_mock.call_args[MOCK_ARGS_KWARGS][HEADERS_KWARG_KEY]
        soapaction_header = headers[SOAPACTION_KEY]

        assert soapaction_header == '"%s#%s"' % (service_type, name)

    def test_call_headers_has_correct_url(self):
        url = "http://www.github.com/"
        action = self.get_mock_action(url=url)
        action.service.device.session.post = post_mock = mock.Mock()

        action()

        actual_url = post_mock.call_args[MOCK_ARGS_ORDERED][0]
        assert actual_url == url

    @mock.patch(
        "urllib3.PoolManager.request", side_effect=urllib3.exceptions.HTTPError
    )
    def test_call_request_is_tried_up_to_max_on_communication_error(
        self, mock_request
    ):
        action = self.get_mock_action()

        try:
            action()
        except svc.ActionException:
            pass

        assert mock_request.call_count == svc.Action.max_rediscovery_attempts

    @mock.patch(
        "urllib3.PoolManager.request", side_effect=urllib3.exceptions.HTTPError
    )
    def test_call_throws_when_final_retry_fails(self, mock_request):
        action = self.get_mock_action()

        with pytest.raises(svc.ActionException):
            action()

    def test_call_returns_correct_dictionary_with_response_contents(
        self, mock_et_fromstring
    ):
        action = self.get_mock_action()
        action.service.device.session.post = mock.Mock()

        envelope = et.Element("soapEnvelope")
        body = et.SubElement(envelope, "soapBody")
        response = et.SubElement(body, "soapResponse")

        response_content = {
            "key1": "value1",
            "key2": "value2",
            "key3": "value3",
        }

        for key, value in response_content.items():
            element = et.SubElement(response, key)
            element.text = value

        mock_et_fromstring.return_value = envelope

        actual_responses = action()

        assert actual_responses == response_content

    def test_fault_response(self, mock_et_fromstring):
        envelope = original_fromstring(
            b"""<s:Envelope
          xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
          s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
        <s:Body>
        <s:Fault>
        <faultcode>s:Client</faultcode>
        <faultstring>UPnPError</faultstring>
        <detail>
        <UPnPError xmlns="urn:schemas-upnp-org:control-1-0">
        <errorCode>-1</errorCode>
        <errorDescription>Invalid Action</errorDescription>
        </UPnPError>
        </detail>
        </s:Fault>
        </s:Body>
        </s:Envelope>
        """
        )
        action = self.get_mock_action()
        action.service.device.session.post = mock.Mock()
        mock_et_fromstring.return_value = envelope

        with pytest.raises(
            SOAPFault, match="SOAP Fault s:Client:UPnPError, -1:Invalid Action"
        ):
            action()

    def test_call_with_overridden_timeout(self):
        action = self.get_mock_action(
            name="OpenNetwork", service_type="urn:Belkin:service:bridge:1"
        )
        action.service.device.session.post = post_mock = mock.Mock()

        action()
        timeout = post_mock.call_args[MOCK_ARGS_KWARGS][TIMEOUT_KWARG_KEY]
        assert timeout == 30

        post_mock.reset_mock()
        action(pywemo_timeout=40)
        timeout = post_mock.call_args[MOCK_ARGS_KWARGS][TIMEOUT_KWARG_KEY]
        assert timeout == 40


class TestService:
    """Tests for the Service class."""

    _service_type = svc.ServiceProperties(
        service_type="urn:Belkin:service:basicevent:1",
        service_id="service_id",
        description_url="description_url",
        control_url="control_url",
        event_subscription_url="event_subscription_url",
    )

    def test_service(self):
        device = mock.create_autospec(WeMoDevice)
        device.session = mock.create_autospec(svc.Session)

        scpd = svc.ServiceDescription(
            actions=[svc.ActionProperties(name="TestActionName", arguments=[])]
        )

        with mock.patch(
            "pywemo.ouimeaux_device.api.service.ServiceDescription.from_xml",
            return_value=scpd,
        ):
            service = svc.Service(device, self._service_type)

        assert "TestActionName" in service.actions
        assert hasattr(service, "TestActionName")

    def test_from_xml_raises(self):
        device = mock.create_autospec(WeMoDevice)
        device.session = mock.create_autospec(svc.Session)

        with mock.patch(
            "pywemo.ouimeaux_device.api.service.ServiceDescription.from_xml",
            side_effect=InvalidSchemaError,
        ), pytest.raises(InvalidSchemaError):
            svc.Service(device, self._service_type)


class MockRequiredService(svc.RequiredServicesMixin):
    """Mock for the RequiredServicesMixin class."""

    _attr_required_services: list[svc.RequiredService] = []

    @property
    def _required_services(self) -> list[svc.RequiredService]:
        return self._attr_required_services


class TestRequiredServicesMixin:
    """Tests for the RequiredServicesMixin class."""

    def test_has_service(self):
        service = mock.create_autospec(svc.Service)
        service.name = "svc_name"
        service.actions = {"action": mock.create_autospec(svc.Action)}
        mixin = MockRequiredService()
        mixin._attr_required_services = [
            svc.RequiredService(name="svc_name", actions=["action"])
        ]
        mixin._check_required_services([service])

    def test_missing_service(self):
        mixin = MockRequiredService()
        mixin._attr_required_services = [
            svc.RequiredService(name="svc_name", actions=["action"])
        ]
        with pytest.raises(svc.MissingServiceError):
            mixin._check_required_services([])

    def test_missing_action(self):
        service = mock.create_autospec(svc.Service)
        service.name = "svc_name"
        service.actions = {"some_action": mock.create_autospec(svc.Action)}
        mixin = MockRequiredService()
        mixin._attr_required_services = [
            svc.RequiredService(name="svc_name", actions=["action"])
        ]

        with pytest.raises(svc.MissingServiceError):
            mixin._check_required_services([service])
