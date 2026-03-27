"""Shared pytest fixtures for the netbox-xclarity test suite."""

from __future__ import annotations

import sys
import os

import pytest

# Ensure the project root is on sys.path so that collector and pynetbox2
# can be imported without a package install step.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# Sample XClarity API payloads used across multiple test modules
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_cpu() -> dict:
    """Minimal XClarity CPU component dict."""
    return {
        "cores": 16,
        "speed": 2.4,
        "architecture": "x86_64",
    }


@pytest.fixture
def sample_dimm() -> dict:
    """Minimal XClarity memory DIMM component dict."""
    return {
        "capacity": 32,
        "memoryType": "DDR4",
        "speed": 3200,
        "eccEnabled": True,
    }


@pytest.fixture
def sample_drive() -> dict:
    """Minimal XClarity disk drive component dict."""
    return {
        "capacity": 480_000_000_000,  # bytes – will be converted to GB
        "mediaType": "SSD",
    }


@pytest.fixture
def sample_psu() -> dict:
    """Minimal XClarity PSU component dict."""
    return {
        "outputWatts": 750,
        "inputVoltageIsAC": True,
        "inputVoltage": 220,
        "hotSwappable": True,
    }


@pytest.fixture
def sample_fan() -> dict:
    """Minimal XClarity fan component dict."""
    return {"speed": 3000}


@pytest.fixture
def sample_expansion_card() -> dict:
    """Minimal XClarity expansion-card component dict."""
    return {
        "bandwidth": 16,
        "pciExpressConnectorType": "PCIe x16",
    }


@pytest.fixture
def sample_node() -> dict:
    """Minimal XClarity node (server) payload."""
    return {
        "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "hostname": "server-01",
        "productName": "ThinkSystem SR650",
        "machineType": "7X06",
        "model": "CTO1WW",
        "manufacturer": "Lenovo",
        "serialNumber": "SN123456",
    }
