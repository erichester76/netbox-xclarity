"""Unit tests for the XClarityClient REST client (collector.py).

All HTTP calls are intercepted with ``requests_mock`` so no real XClarity
server is needed.  The tests verify:
* correct URL construction
* correct response parsing for both list-wrapped and bare-list API styles
* correct extraction of node / chassis detail payloads
* HTTP error propagation
"""

from __future__ import annotations

import pytest
import requests
import requests_mock as rm_module

from collector import XClarityClient


HOST = "xclarity.example.com"
BASE_URL = f"https://{HOST}:443"


@pytest.fixture
def client() -> XClarityClient:
    """Return an XClarityClient configured to skip SSL verification."""
    return XClarityClient(
        host=HOST,
        username="admin",
        password="secret",
        port=443,
        verify_ssl=False,
        timeout=5,
    )


# ===========================================================================
# get_nodes
# ===========================================================================

class TestGetNodes:
    def test_list_wrapped_response(self, client, requests_mock):
        payload = {"nodeList": [{"uuid": "aaa", "hostname": "node-01"}]}
        requests_mock.get(f"{BASE_URL}/nodes", json=payload)
        result = client.get_nodes()
        assert result == payload["nodeList"]

    def test_bare_list_response(self, client, requests_mock):
        payload = [{"uuid": "bbb", "hostname": "node-02"}]
        requests_mock.get(f"{BASE_URL}/nodes", json=payload)
        result = client.get_nodes()
        assert result == payload

    def test_empty_node_list(self, client, requests_mock):
        requests_mock.get(f"{BASE_URL}/nodes", json={"nodeList": []})
        assert client.get_nodes() == []

    def test_http_error_raises(self, client, requests_mock):
        requests_mock.get(f"{BASE_URL}/nodes", status_code=500)
        with pytest.raises(requests.HTTPError):
            client.get_nodes()


# ===========================================================================
# get_chassis
# ===========================================================================

class TestGetChassis:
    def test_list_wrapped_response(self, client, requests_mock):
        payload = {"chassisList": [{"uuid": "ccc", "hostname": "chassis-01"}]}
        requests_mock.get(f"{BASE_URL}/chassis", json=payload)
        result = client.get_chassis()
        assert result == payload["chassisList"]

    def test_bare_list_response(self, client, requests_mock):
        payload = [{"uuid": "ddd", "hostname": "chassis-02"}]
        requests_mock.get(f"{BASE_URL}/chassis", json=payload)
        assert client.get_chassis() == payload

    def test_http_error_raises(self, client, requests_mock):
        requests_mock.get(f"{BASE_URL}/chassis", status_code=401)
        with pytest.raises(requests.HTTPError):
            client.get_chassis()


# ===========================================================================
# get_switches
# ===========================================================================

class TestGetSwitches:
    def test_list_wrapped_response(self, client, requests_mock):
        payload = {"switchList": [{"uuid": "eee", "hostname": "switch-01"}]}
        requests_mock.get(f"{BASE_URL}/switches", json=payload)
        assert client.get_switches() == payload["switchList"]

    def test_bare_list_response(self, client, requests_mock):
        payload = [{"uuid": "fff"}]
        requests_mock.get(f"{BASE_URL}/switches", json=payload)
        assert client.get_switches() == payload


# ===========================================================================
# get_storage
# ===========================================================================

class TestGetStorage:
    def test_list_wrapped_response(self, client, requests_mock):
        payload = {"storageList": [{"uuid": "ggg", "hostname": "storage-01"}]}
        requests_mock.get(f"{BASE_URL}/storage", json=payload)
        assert client.get_storage() == payload["storageList"]

    def test_bare_list_response(self, client, requests_mock):
        payload = [{"uuid": "hhh"}]
        requests_mock.get(f"{BASE_URL}/storage", json=payload)
        assert client.get_storage() == payload


# ===========================================================================
# get_node_details
# ===========================================================================

class TestGetNodeDetails:
    def test_returns_detail_dict(self, client, requests_mock):
        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        detail = {"uuid": uuid, "hostname": "server-01", "serialNumber": "SN99"}
        requests_mock.get(f"{BASE_URL}/nodes/{uuid}", json=detail)
        result = client.get_node_details(uuid)
        assert result == detail

    def test_http_error_raises(self, client, requests_mock):
        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        requests_mock.get(f"{BASE_URL}/nodes/{uuid}", status_code=404)
        with pytest.raises(requests.HTTPError):
            client.get_node_details(uuid)


# ===========================================================================
# get_chassis_details
# ===========================================================================

class TestGetChassisDetails:
    def test_returns_detail_dict(self, client, requests_mock):
        uuid = "12345678-abcd-ef01-2345-67890abcdef0"
        detail = {"uuid": uuid, "hostname": "chassis-01"}
        requests_mock.get(f"{BASE_URL}/chassis/{uuid}", json=detail)
        result = client.get_chassis_details(uuid)
        assert result == detail

    def test_http_error_raises(self, client, requests_mock):
        uuid = "12345678-abcd-ef01-2345-67890abcdef0"
        requests_mock.get(f"{BASE_URL}/chassis/{uuid}", status_code=403)
        with pytest.raises(requests.HTTPError):
            client.get_chassis_details(uuid)


# ===========================================================================
# URL construction and auth
# ===========================================================================

class TestClientConstruction:
    def test_base_url_uses_host_and_port(self):
        c = XClarityClient("myhost", "u", "p", port=8443, verify_ssl=False)
        assert c.base_url == "https://myhost:8443"

    def test_session_auth_set(self):
        c = XClarityClient("myhost", "user", "pass", verify_ssl=False)
        assert c._session.auth == ("user", "pass")

    def test_default_port_443(self):
        c = XClarityClient("myhost", "u", "p", verify_ssl=False)
        assert ":443" in c.base_url
