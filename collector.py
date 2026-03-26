#!/usr/bin/env python3
"""Lenovo XClarity Administrator → NetBox collector.

Pulls managed device data from the XClarity REST API and upserts it into
NetBox using the pynetbox2 client that lives alongside this script.

Usage
-----
  python collector.py [--env-file ENV_FILE] [--dry-run] [--verbose]

Configuration is read from environment variables.  A ``.env`` file is loaded
first (if present / specified), then any variables already set in the shell
take precedence.  See ``.env.example`` for the full list of supported keys.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Any, Optional

import requests
from dotenv import load_dotenv
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# ---------------------------------------------------------------------------
# Ensure the directory that contains pynetbox2.py is on sys.path so that the
# module can be imported regardless of where the script is invoked from.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pynetbox2  # noqa: E402  (after sys.path manipulation)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# XClarity client
# ---------------------------------------------------------------------------

class XClarityClient:
    """Minimal REST client for Lenovo XClarity Administrator.

    Only the endpoints needed by the collector are implemented:
    * ``/nodes``    – managed servers / compute nodes
    * ``/chassis``  – chassis (blade-centre chassis)
    * ``/switches`` – managed top-of-rack / embedded switches
    * ``/storage``  – managed storage subsystems

    The REST API is accessed directly at the host root (e.g. ``/nodes``),
    not under any ``/aicc`` prefix.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        verify_ssl: bool = True,
        timeout: int = 30,
    ) -> None:
        self.base_url = f"https://{host}:{port}"
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._session = requests.Session()
        self._session.auth = (username, password)
        self._session.verify = verify_ssl
        self._session.headers.update({"Accept": "application/json"})
        if not verify_ssl:
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        logger.debug("XClarity GET %s params=%s", url, params)
        response = self._session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_nodes(self) -> list[dict]:
        """Return all managed compute nodes (servers)."""
        data = self._get("/nodes")
        return data.get("nodeList", data) if isinstance(data, dict) else data

    def get_chassis(self) -> list[dict]:
        """Return all managed chassis."""
        data = self._get("/chassis")
        return data.get("chassisList", data) if isinstance(data, dict) else data

    def get_switches(self) -> list[dict]:
        """Return all managed switches."""
        data = self._get("/switches")
        return data.get("switchList", data) if isinstance(data, dict) else data

    def get_storage(self) -> list[dict]:
        """Return all managed storage devices."""
        data = self._get("/storage")
        return data.get("storageList", data) if isinstance(data, dict) else data

    def get_node_details(self, uuid: str) -> dict:
        """Return detailed information for a single node."""
        return self._get(f"/nodes/{uuid}")

    def get_chassis_details(self, uuid: str) -> dict:
        """Return detailed information for a single chassis."""
        return self._get(f"/chassis/{uuid}")


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def _env(key: str, fallback: Optional[str] = None) -> str:
    """Return the value of environment variable *key*, or *fallback*."""
    return os.environ.get(key, fallback or "")


def load_env_file(path: Optional[str]) -> None:
    """Load variables from a ``.env`` file into the process environment.

    Shell variables already set take precedence (``override=False``).
    If *path* is given explicitly and the file does not exist the process exits.
    When *path* is ``None`` the default ``.env`` in the current directory is
    loaded if it exists (silently skipped when absent).
    """
    if path:
        if not os.path.exists(path):
            logger.error(".env file not found: %s", path)
            sys.exit(1)
        load_dotenv(dotenv_path=path, override=False)
    else:
        load_dotenv(override=False)  # loads .env in cwd if present, no-op otherwise


def _validate_env() -> None:
    """Abort if any required environment variable is unset or empty."""
    required = [
        "XCLARITY_HOST",
        "XCLARITY_USERNAME",
        "XCLARITY_PASSWORD",
        "NETBOX_URL",
        "NETBOX_TOKEN",
    ]
    missing = [key for key in required if not _env(key)]
    if missing:
        for key in missing:
            logger.error("Missing required environment variable: %s", key)
        sys.exit(1)


# ---------------------------------------------------------------------------
# NetBox helper: ensure prerequisite objects exist
# ---------------------------------------------------------------------------

class NetBoxSync:
    """Thin wrapper around pynetbox2 that handles prerequisite lookups/creation."""

    # Lenovo is always the manufacturer for XClarity-managed hardware
    MANUFACTURER_NAME = "Lenovo"
    MANUFACTURER_SLUG = "lenovo"

    def __init__(self, nb: pynetbox2.NetBoxAPI, dry_run: bool = False) -> None:
        self.nb = nb
        self.dry_run = dry_run
        self._manufacturer_cache: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Manufacturer
    # ------------------------------------------------------------------

    def ensure_manufacturer(self, name: Optional[str] = None) -> Optional[int]:
        """Return the NetBox ID for a manufacturer, creating it if needed.

        If *name* is ``None`` the default Lenovo manufacturer is used.
        Results are cached so repeated calls do not hit the API twice.
        """
        mfr_name = name or self.MANUFACTURER_NAME
        if mfr_name in self._manufacturer_cache:
            return self._manufacturer_cache[mfr_name]
        mfr_slug = _slugify(mfr_name)
        obj = self._upsert(
            "dcim.manufacturers",
            {"name": mfr_name, "slug": mfr_slug},
            lookup_fields=["slug"],
        )
        mfr_id = self._id(obj)
        if mfr_id is not None:
            self._manufacturer_cache[mfr_name] = mfr_id
        return mfr_id

    # ------------------------------------------------------------------
    # Device type
    # ------------------------------------------------------------------

    def ensure_device_type(self, model: str, part_number: str = "", manufacturer_name: Optional[str] = None) -> Optional[int]:
        """Return the NetBox ID for a device type, creating it if needed."""
        if not model:
            return None
        slug = _slugify(model)
        manufacturer_id = self.ensure_manufacturer(manufacturer_name)
        payload: dict[str, Any] = {
            "manufacturer": manufacturer_id,
            "model": model,
            "slug": slug,
        }
        if part_number:
            payload["part_number"] = part_number
        obj = self._upsert("dcim.device_types", payload, lookup_fields=["manufacturer", "slug"])
        return self._id(obj)

    # ------------------------------------------------------------------
    # Device role
    # ------------------------------------------------------------------

    def ensure_device_role(self, name: str, slug: str, color: str = "9e9e9e") -> Optional[int]:
        """Return the NetBox ID for a device role, creating it if needed."""
        obj = self._upsert(
            "dcim.device_roles",
            {"name": name, "slug": slug, "color": color},
            lookup_fields=["slug"],
        )
        return self._id(obj)

    # ------------------------------------------------------------------
    # Site
    # ------------------------------------------------------------------

    def ensure_site(self, name: str, slug: str) -> Optional[int]:
        """Return the NetBox ID for a site, creating it if needed."""
        obj = self._upsert("dcim.sites", {"name": name, "slug": slug}, lookup_fields=["slug"])
        return self._id(obj)

    # ------------------------------------------------------------------
    # Location (room / area within a site)
    # ------------------------------------------------------------------

    def ensure_location(self, name: str, site_id: int) -> Optional[int]:
        """Return the NetBox ID for a location (room/area within a site), creating it if needed."""
        slug = _slugify(name)
        obj = self._upsert(
            "dcim.locations",
            {"name": name, "slug": slug, "site": site_id},
            lookup_fields=["name", "site"],
        )
        return self._id(obj)

    # ------------------------------------------------------------------
    # Rack
    # ------------------------------------------------------------------

    def ensure_rack(self, name: str, site_id: int, location_id: Optional[int] = None) -> Optional[int]:
        """Return the NetBox ID for a rack, creating it if needed."""
        payload: dict[str, Any] = {"name": name, "site": site_id}
        if location_id is not None:
            payload["location"] = location_id
        obj = self._upsert("dcim.racks", payload, lookup_fields=["name", "site"])
        return self._id(obj)

    # ------------------------------------------------------------------
    # Platform
    # ------------------------------------------------------------------

    def ensure_platform(self, name: str, slug: str) -> Optional[int]:
        """Return the NetBox ID for a platform, creating it if needed."""
        manufacturer_id = self.ensure_manufacturer()
        obj = self._upsert(
            "dcim.platforms",
            {"name": name, "slug": slug, "manufacturer": manufacturer_id},
            lookup_fields=["slug"],
        )
        return self._id(obj)

    # ------------------------------------------------------------------
    # Device
    # ------------------------------------------------------------------

    def upsert_device(self, payload: dict[str, Any]) -> Optional[Any]:
        """Create or update a device record.  *payload* must include ``name`` and ``serial``."""
        lookup = ["serial"] if payload.get("serial") else ["name"]
        return self._upsert("dcim.devices", payload, lookup_fields=lookup)

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    def upsert_interface(self, payload: dict[str, Any]) -> Optional[Any]:
        """Create or update an interface on a device."""
        return self._upsert("dcim.interfaces", payload, lookup_fields=["device", "name"])

    # ------------------------------------------------------------------
    # IP address
    # ------------------------------------------------------------------

    def upsert_ip_address(self, payload: dict[str, Any]) -> Optional[Any]:
        """Create or update an IP address record."""
        return self._upsert("ipam.ip_addresses", payload, lookup_fields=["address"])

    # ------------------------------------------------------------------
    # Inventory item
    # ------------------------------------------------------------------

    def upsert_inventory_item(self, payload: dict[str, Any]) -> Optional[Any]:
        """Create or update an inventory item on a device."""
        return self._upsert(
            "dcim.inventory_items",
            payload,
            lookup_fields=["device", "name"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upsert(
        self,
        resource: str,
        payload: dict[str, Any],
        lookup_fields: Optional[list[str]] = None,
    ) -> Optional[Any]:
        if self.dry_run:
            logger.info("[DRY-RUN] upsert %s %s", resource, payload)
            return None
        try:
            return self.nb.upsert(resource, payload, lookup_fields=lookup_fields)
        except Exception as exc:
            logger.error("Failed to upsert %s payload=%s: %s", resource, payload, exc)
            return None

    @staticmethod
    def _id(obj: Any) -> Optional[int]:
        if obj is None:
            return None
        if isinstance(obj, int):
            return obj
        return getattr(obj, "id", None)


# ---------------------------------------------------------------------------
# Main collector logic
# ---------------------------------------------------------------------------

class Collector:
    """Orchestrates data collection from XClarity and sync to NetBox."""

    # Map from XClarity ``type`` values to human-readable labels
    _TYPE_LABELS: dict[str, str] = {
        "Rack-Tower Server": "server",
        "Blade Server": "server",
        "Dense Server": "server",
        "System x": "server",
        "ThinkSystem": "server",
    }

    def __init__(
        self,
        xc: XClarityClient,
        nb_sync: NetBoxSync,
    ) -> None:
        self.xc = xc
        self.nb_sync = nb_sync
        self._categories = [c.strip() for c in _env("COLLECTOR_CATEGORIES", "nodes,chassis,switches,storage").split(",")]
        self._sync_interfaces = _env("COLLECTOR_SYNC_INTERFACES", "true").lower() not in ("false", "0", "no")
        self._sync_inventory = _env("COLLECTOR_SYNC_INVENTORY", "true").lower() not in ("false", "0", "no")

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("Collector starting. Categories: %s", self._categories)

        if "nodes" in self._categories:
            self._collect_nodes()
        if "chassis" in self._categories:
            self._collect_chassis()
        if "switches" in self._categories:
            self._collect_switches()
        if "storage" in self._categories:
            self._collect_storage()

        logger.info("Collector finished.")

    # ------------------------------------------------------------------
    # Nodes (servers)
    # ------------------------------------------------------------------

    def _collect_nodes(self) -> None:
        logger.info("Collecting nodes (servers) …")
        try:
            nodes = self.xc.get_nodes()
        except Exception as exc:
            logger.error("Failed to retrieve nodes from XClarity: %s", exc)
            return

        logger.info("Found %d node(s).", len(nodes))
        for node in nodes:
            self._sync_node(node)

    def _sync_node(self, node: dict) -> None:
        raw_name = node.get("name") or node.get("hostname") or node.get("uuid", "unknown")
        name = self._apply_name_regex(str(raw_name))
        logger.debug("Syncing node: %s", name)

        model = _build_model_name(node)
        part_number = node.get("partNumber") or node.get("productName") or ""
        serial = node.get("serialNumber") or node.get("serial") or ""
        mfr_name = node.get("manufacturer") or None

        device_type_id = self.nb_sync.ensure_device_type(model, part_number, mfr_name)
        role_slug = _env("NETBOX_SERVER_ROLE", "server")
        role_id = self.nb_sync.ensure_device_role(
            name=role_slug.title(),
            slug=role_slug,
            color="2196f3",
        )
        site_id, location_id, rack_id, rack_position = self._resolve_placement(node)

        if device_type_id is None or role_id is None or site_id is None:
            logger.warning("Skipping node %s: missing device_type/role/site", name)
            return

        payload: dict[str, Any] = {
            "name": name,
            "device_type": device_type_id,
            "role": role_id,
            "site": site_id,
            "status": "active",
        }
        if serial:
            payload["serial"] = serial
        if location_id is not None:
            payload["location"] = location_id
        if rack_id is not None:
            payload["rack"] = rack_id
            payload["face"] = "front"
        if rack_position is not None and rack_id is not None:
            payload["position"] = rack_position

        device = self.nb_sync.upsert_device(payload)

        if device is None:
            return

        device_id = self.nb_sync._id(device)
        if device_id is None:
            return

        if self._sync_interfaces:
            self._sync_node_interfaces(node, device_id)

        if self._sync_inventory:
            self._sync_node_inventory(node, device_id)

    def _sync_node_interfaces(self, node: dict, device_id: int) -> None:
        """Sync NIC / management interfaces reported by XClarity for a node."""
        # XClarity reports network adapters under several possible keys
        adapters = (
            node.get("adapterList")
            or node.get("adapters")
            or node.get("networkAdapters")
            or []
        )
        for adapter in adapters:
            ports = adapter.get("portList") or adapter.get("ports") or []
            for port in ports:
                port_index = port.get("portIndex", "?")
                iface_name = port.get("portName") or port.get("name") or f"port-{port_index}"
                mac = _normalise_mac(port.get("macAddress") or port.get("mac") or "")
                iface_payload: dict[str, Any] = {
                    "device": device_id,
                    "name": iface_name,
                    "type": _port_type(port),
                }
                if mac:
                    iface_payload["mac_address"] = mac

                iface = self.nb_sync.upsert_interface(iface_payload)
                iface_id = self.nb_sync._id(iface)

                if iface_id is None:
                    continue

                # Sync IP addresses on this port
                for ip_key in ("ipInterfaces", "ipAddresses", "ips"):
                    for ip_info in port.get(ip_key) or []:
                        address = ip_info.get("IPv4addresses") or ip_info.get("address") or ip_info.get("ipv4Address")
                        if isinstance(address, list):
                            address = address[0] if address else None
                        if not address:
                            continue
                        cidr = _to_cidr(address, ip_info.get("subnet") or ip_info.get("netmask"))
                        if cidr:
                            self.nb_sync.upsert_ip_address({
                                "address": cidr,
                                "assigned_object_type": "dcim.interface",
                                "assigned_object_id": iface_id,
                                "status": "active",
                            })

        # Dedicated management (BMC) IP
        mgmt_ip = (
            node.get("mgmtProcIPaddress")
            or node.get("ipAddress")
            or node.get("primaryMgmtIPaddress")
        )
        if mgmt_ip:
            cidr = _to_cidr(mgmt_ip)
            if cidr:
                # Ensure a management interface exists
                mgmt_iface = self.nb_sync.upsert_interface({
                    "device": device_id,
                    "name": "mgmt0",
                    "type": "other",
                    "mgmt_only": True,
                })
                mgmt_iface_id = self.nb_sync._id(mgmt_iface)
                if mgmt_iface_id:
                    self.nb_sync.upsert_ip_address({
                        "address": cidr,
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": mgmt_iface_id,
                        "status": "active",
                    })

    def _sync_node_inventory(self, node: dict, device_id: int) -> None:
        """Sync CPUs, DIMMs, disk drives, add-in cards, PSUs and fans as inventory items."""
        default_mfr_id = self.nb_sync.ensure_manufacturer()

        def _item_mfr_id(item: dict) -> Optional[int]:
            """Return manufacturer ID for *item*, falling back to the device manufacturer."""
            mfr = item.get("manufacturer") or item.get("mfrName") or None
            return self.nb_sync.ensure_manufacturer(mfr) if mfr else default_mfr_id

        # CPUs
        for cpu in node.get("processors") or node.get("processorSlots") or []:
            name = cpu.get("productName") or cpu.get("description") or f"CPU {cpu.get('slot', '?')}"
            desc_parts = [
                cpu.get("model") or "",
                f"{cpu['speed']} MHz" if cpu.get("speed") else "",
                f"{cpu['cores']} cores" if cpu.get("cores") else "",
            ]
            self.nb_sync.upsert_inventory_item({
                "device": device_id,
                "name": name,
                "manufacturer": _item_mfr_id(cpu),
                "part_id": cpu.get("partNumber") or "",
                "serial": cpu.get("serialNumber") or "",
                "description": ", ".join(p for p in desc_parts if p),
            })

        # Memory / DIMMs
        for dimm in node.get("memoryModules") or node.get("dimmSlots") or []:
            name = dimm.get("productName") or dimm.get("description") or f"DIMM {dimm.get('slot', '?')}"
            capacity = dimm.get("capacity") or dimm.get("size") or ""
            desc_parts = [
                f"{capacity} MB" if capacity else "",
                f"{dimm['speed']} MHz" if dimm.get("speed") else "",
                dimm.get("memoryType") or dimm.get("type") or "",
            ]
            self.nb_sync.upsert_inventory_item({
                "device": device_id,
                "name": name,
                "manufacturer": _item_mfr_id(dimm),
                "part_id": dimm.get("partNumber") or "",
                "serial": dimm.get("serialNumber") or "",
                "description": ", ".join(p for p in desc_parts if p),
            })

        # Disk drives
        for drive in (
            node.get("diskDrives")
            or node.get("drives")
            or node.get("storageDisks")
            or node.get("diskDriveList")
            or []
        ):
            name = (
                drive.get("productName")
                or drive.get("description")
                or drive.get("name")
                or f"Drive {drive.get('slot', '?')}"
            )
            desc_parts = [
                f"{drive['capacity']} GB" if drive.get("capacity") else "",
                drive.get("type") or drive.get("interfaceType") or "",
                f"{drive['rpm']} RPM" if drive.get("rpm") else "",
                drive.get("model") or "",
            ]
            self.nb_sync.upsert_inventory_item({
                "device": device_id,
                "name": name,
                "manufacturer": _item_mfr_id(drive),
                "part_id": drive.get("partNumber") or "",
                "serial": drive.get("serialNumber") or "",
                "description": ", ".join(p for p in desc_parts if p),
            })

        # Add-in cards (PCIe)
        for card in (
            node.get("addinCards")
            or node.get("pciExpressCards")
            or node.get("pciCards")
            or node.get("addinCardList")
            or []
        ):
            name = (
                card.get("productName")
                or card.get("description")
                or card.get("name")
                or f"Addin Card {card.get('slot', '?')}"
            )
            desc_parts = [
                f"PCI bus {card['pciBusNumber']}" if card.get("pciBusNumber") else "",
                f"Slot {card['slot']}" if card.get("slot") else "",
                card.get("slotName") or "",
                card.get("type") or "",
            ]
            self.nb_sync.upsert_inventory_item({
                "device": device_id,
                "name": name,
                "manufacturer": _item_mfr_id(card),
                "part_id": card.get("partNumber") or "",
                "serial": card.get("serialNumber") or "",
                "description": ", ".join(p for p in desc_parts if p),
            })

            # Sync ethernet ports from this add-in card as device interfaces
            if self._sync_interfaces:
                self._sync_addin_card_interfaces(card, device_id)

        # Power supplies
        for psu in node.get("powerSupplies") or node.get("powerSupplySlots") or []:
            name = psu.get("productName") or psu.get("description") or f"PSU {psu.get('slot', '?')}"
            desc_parts = [
                psu.get("model") or "",
                psu.get("inputVoltageType") or "",
                f"{psu['outputWatts']} W" if psu.get("outputWatts") else "",
            ]
            self.nb_sync.upsert_inventory_item({
                "device": device_id,
                "name": name,
                "manufacturer": _item_mfr_id(psu),
                "part_id": psu.get("partNumber") or "",
                "serial": psu.get("serialNumber") or "",
                "description": ", ".join(p for p in desc_parts if p),
            })

        # Fans
        for fan in node.get("fans") or node.get("fanSlots") or []:
            name = fan.get("name") or fan.get("description") or f"Fan {fan.get('slot', '?')}"
            desc_parts = [f"{fan['speed']} RPM" if fan.get("speed") else ""]
            self.nb_sync.upsert_inventory_item({
                "device": device_id,
                "name": name,
                "manufacturer": _item_mfr_id(fan),
                "part_id": fan.get("partNumber") or "",
                "serial": fan.get("serialNumber") or "",
                "description": ", ".join(p for p in desc_parts if p),
            })

    def _sync_addin_card_interfaces(self, card: dict, device_id: int) -> None:
        """Sync ethernet ports from a PCIe add-in card as device interfaces."""
        ports = card.get("portList") or card.get("ports") or []
        for port in ports:
            port_type_str = (port.get("type") or port.get("portType") or "").lower()
            if "ethernet" not in port_type_str and "eth" not in port_type_str:
                continue
            port_index = port.get("portIndex", "?")
            iface_name = port.get("portName") or port.get("name") or f"port-{port_index}"
            mac = _normalise_mac(port.get("macAddress") or port.get("mac") or "")
            iface_payload: dict[str, Any] = {
                "device": device_id,
                "name": iface_name,
                "type": _port_type(port),
            }
            if mac:
                iface_payload["mac_address"] = mac
            iface = self.nb_sync.upsert_interface(iface_payload)
            iface_id = self.nb_sync._id(iface)
            if iface_id is None:
                continue
            # Sync IP addresses on this port
            for ip_key in ("ipInterfaces", "ipAddresses", "ips"):
                for ip_info in port.get(ip_key) or []:
                    address = (
                        ip_info.get("IPv4addresses")
                        or ip_info.get("address")
                        or ip_info.get("ipv4Address")
                    )
                    if isinstance(address, list):
                        address = address[0] if address else None
                    if not address:
                        continue
                    cidr = _to_cidr(address, ip_info.get("subnet") or ip_info.get("netmask"))
                    if cidr:
                        self.nb_sync.upsert_ip_address({
                            "address": cidr,
                            "assigned_object_type": "dcim.interface",
                            "assigned_object_id": iface_id,
                            "status": "active",
                        })

    # ------------------------------------------------------------------
    # Chassis
    # ------------------------------------------------------------------

    def _collect_chassis(self) -> None:
        logger.info("Collecting chassis …")
        try:
            chassis_list = self.xc.get_chassis()
        except Exception as exc:
            logger.error("Failed to retrieve chassis from XClarity: %s", exc)
            return

        logger.info("Found %d chassis.", len(chassis_list))
        for chassis in chassis_list:
            self._sync_chassis(chassis)

    def _sync_chassis(self, chassis: dict) -> None:
        raw_name = chassis.get("name") or chassis.get("hostname") or chassis.get("uuid", "unknown")
        name = self._apply_name_regex(str(raw_name))
        logger.debug("Syncing chassis: %s", name)

        model = _build_model_name(chassis)
        part_number = chassis.get("partNumber") or ""
        serial = chassis.get("serialNumber") or ""
        mfr_name = chassis.get("manufacturer") or None

        device_type_id = self.nb_sync.ensure_device_type(model, part_number, mfr_name)
        role_slug = _env("NETBOX_CHASSIS_ROLE", "chassis")
        role_id = self.nb_sync.ensure_device_role(
            name=role_slug.title(),
            slug=role_slug,
            color="9c27b0",
        )
        site_id, location_id, rack_id, rack_position = self._resolve_placement(chassis)

        if device_type_id is None or role_id is None or site_id is None:
            logger.warning("Skipping chassis %s: missing device_type/role/site", name)
            return

        payload: dict[str, Any] = {
            "name": name,
            "device_type": device_type_id,
            "role": role_id,
            "site": site_id,
            "status": "active",
        }
        if serial:
            payload["serial"] = serial
        if location_id is not None:
            payload["location"] = location_id
        if rack_id is not None:
            payload["rack"] = rack_id
            payload["face"] = "front"
        if rack_position is not None and rack_id is not None:
            payload["position"] = rack_position

        self.nb_sync.upsert_device(payload)

    # ------------------------------------------------------------------
    # Switches
    # ------------------------------------------------------------------

    def _collect_switches(self) -> None:
        logger.info("Collecting switches …")
        try:
            switches = self.xc.get_switches()
        except Exception as exc:
            logger.error("Failed to retrieve switches from XClarity: %s", exc)
            return

        logger.info("Found %d switch(es).", len(switches))
        for switch in switches:
            self._sync_switch(switch)

    def _sync_switch(self, switch: dict) -> None:
        raw_name = switch.get("name") or switch.get("hostname") or switch.get("uuid", "unknown")
        name = self._apply_name_regex(str(raw_name))
        logger.debug("Syncing switch: %s", name)

        model = _build_model_name(switch)
        part_number = switch.get("partNumber") or ""
        serial = switch.get("serialNumber") or ""
        mfr_name = switch.get("manufacturer") or None

        device_type_id = self.nb_sync.ensure_device_type(model, part_number, mfr_name)
        role_slug = _env("NETBOX_SWITCH_ROLE", "switch")
        role_id = self.nb_sync.ensure_device_role(
            name=role_slug.title(),
            slug=role_slug,
            color="4caf50",
        )
        site_id, location_id, rack_id, rack_position = self._resolve_placement(switch)

        if device_type_id is None or role_id is None or site_id is None:
            logger.warning("Skipping switch %s: missing device_type/role/site", name)
            return

        payload: dict[str, Any] = {
            "name": name,
            "device_type": device_type_id,
            "role": role_id,
            "site": site_id,
            "status": "active",
        }
        if serial:
            payload["serial"] = serial
        if location_id is not None:
            payload["location"] = location_id
        if rack_id is not None:
            payload["rack"] = rack_id
            payload["face"] = "front"
        if rack_position is not None and rack_id is not None:
            payload["position"] = rack_position

        device = self.nb_sync.upsert_device(payload)

        if device is not None and self._sync_interfaces:
            device_id = self.nb_sync._id(device)
            if device_id:
                self._sync_switch_interfaces(switch, device_id)

    def _sync_switch_interfaces(self, switch: dict, device_id: int) -> None:
        """Sync switch ports reported by XClarity."""
        ports = switch.get("portList") or switch.get("ports") or []
        for port in ports:
            port_index = port.get("portIndex", "?")
            port_name = port.get("portName") or port.get("name") or f"port-{port_index}"
            mac = _normalise_mac(port.get("macAddress") or port.get("mac") or "")
            iface_payload: dict[str, Any] = {
                "device": device_id,
                "name": port_name,
                "type": _port_type(port),
            }
            if mac:
                iface_payload["mac_address"] = mac
            self.nb_sync.upsert_interface(iface_payload)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _collect_storage(self) -> None:
        logger.info("Collecting storage devices …")
        try:
            storage_list = self.xc.get_storage()
        except Exception as exc:
            logger.error("Failed to retrieve storage from XClarity: %s", exc)
            return

        logger.info("Found %d storage device(s).", len(storage_list))
        for storage in storage_list:
            self._sync_storage(storage)

    def _sync_storage(self, storage: dict) -> None:
        raw_name = storage.get("name") or storage.get("hostname") or storage.get("uuid", "unknown")
        name = self._apply_name_regex(str(raw_name))
        logger.debug("Syncing storage: %s", name)

        model = _build_model_name(storage)
        part_number = storage.get("partNumber") or ""
        serial = storage.get("serialNumber") or ""
        mfr_name = storage.get("manufacturer") or None

        device_type_id = self.nb_sync.ensure_device_type(model, part_number, mfr_name)
        role_slug = _env("NETBOX_STORAGE_ROLE", "storage")
        role_id = self.nb_sync.ensure_device_role(
            name=role_slug.title(),
            slug=role_slug,
            color="ff9800",
        )
        site_id, location_id, rack_id, rack_position = self._resolve_placement(storage)

        if device_type_id is None or role_id is None or site_id is None:
            logger.warning("Skipping storage %s: missing device_type/role/site", name)
            return

        payload: dict[str, Any] = {
            "name": name,
            "device_type": device_type_id,
            "role": role_id,
            "site": site_id,
            "status": "active",
        }
        if serial:
            payload["serial"] = serial
        if location_id is not None:
            payload["location"] = location_id
        if rack_id is not None:
            payload["rack"] = rack_id
            payload["face"] = "front"
        if rack_position is not None and rack_id is not None:
            payload["position"] = rack_position

        self.nb_sync.upsert_device(payload)

    # ------------------------------------------------------------------
    # Name regex transformation
    # ------------------------------------------------------------------

    def _apply_name_regex(self, name: str) -> str:
        """Apply hostname regex transformation defined in environment variables."""
        return _apply_regex(
            name,
            _env("COLLECTOR_HOSTNAME_REGEX", ""),
            _env("COLLECTOR_HOSTNAME_REPLACE", ""),
        )

    # ------------------------------------------------------------------
    # Placement resolution (site, location, rack, rack position)
    # ------------------------------------------------------------------

    def _resolve_placement(
        self, device: dict
    ) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """Return ``(site_id, location_id, rack_id, rack_position)`` from device metadata.

        XClarity location mapping:

        * ``location.location``      → NetBox **site**
        * ``location.room``          → NetBox **location** (area within site)
        * ``location.rack``          → NetBox **rack**
        * ``location.lowestRackUnit`` → rack **position** (front face)
        """
        loc = device.get("location") or {}
        if not isinstance(loc, dict):
            loc = {}

        # --- Site ---
        site_raw = (
            loc.get("location")
            or device.get("dataCenter")
            or loc.get("dataCenter")
            or ""
        )
        if not site_raw:
            site_raw = _env("NETBOX_DEFAULT_SITE", "default")
        site_name = _apply_regex(
            str(site_raw),
            _env("COLLECTOR_LOCATION_REGEX", ""),
            _env("COLLECTOR_LOCATION_REPLACE", ""),
        )
        site_id = self.nb_sync.ensure_site(site_name, _slugify(site_name))

        # --- Location (room within the site) ---
        room_raw = loc.get("room") or device.get("room") or ""
        location_id: Optional[int] = None
        if room_raw and site_id is not None:
            room_name = _apply_regex(
                str(room_raw),
                _env("COLLECTOR_ROOM_REGEX", ""),
                _env("COLLECTOR_ROOM_REPLACE", ""),
            )
            location_id = self.nb_sync.ensure_location(room_name, site_id)

        # --- Rack ---
        rack_raw = loc.get("rack") or device.get("rack") or ""
        rack_id: Optional[int] = None
        if rack_raw and site_id is not None:
            rack_id = self.nb_sync.ensure_rack(str(rack_raw), site_id, location_id)

        # --- Rack position ---
        rack_position: Optional[int] = None
        raw_pos = loc.get("lowestRackUnit")
        if raw_pos is not None:
            try:
                rack_position = int(raw_pos)
            except (ValueError, TypeError):
                pass

        return site_id, location_id, rack_id, rack_position


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _slugify(value: str) -> str:
    """Convert *value* to a NetBox-compatible slug (lowercase, hyphens)."""
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value[:100]


def _apply_regex(value: str, pattern: str, replacement: str) -> str:
    """Apply ``re.sub(pattern, replacement, value)``.

    Returns *value* unchanged when *pattern* is empty or invalid.
    """
    if not pattern or not value:
        return value
    try:
        return re.sub(pattern, replacement, value)
    except re.error as exc:
        logger.warning("Invalid regex '%s': %s", pattern, exc)
        return value


def _build_model_name(device: dict) -> str:
    """Build a combined device-type model name from XClarity fields.

    Format: ``<manufacturer> <productName> -[<machineType><model>]-``

    Example: ``Lenovo ThinkSystem SR650 -[7X06CTO1WW]-``

    Falls back to ``machineType`` or ``model`` alone when the richer fields
    are absent, so behaviour is backwards-compatible.
    """
    manufacturer = device.get("manufacturer", "")
    product_name = device.get("productName", "")
    machine_type = device.get("machineType", "")
    model_code = device.get("model", "")
    parts: list[str] = []
    if manufacturer:
        parts.append(manufacturer)
    if product_name:
        parts.append(product_name)
    suffix = f"{machine_type}{model_code}".strip()
    if suffix:
        parts.append(f"-[{suffix}]-")
    return " ".join(parts) if parts else (machine_type or model_code or "")


def _port_type(port: dict) -> str:
    """Infer a NetBox interface type string from XClarity port speed/type data."""
    speed = str(port.get("speed") or port.get("portSpeed") or "").lower()
    # Extract numeric tokens so substring matches do not cause false positives
    # (e.g. "10000" must not be confused with "100000").
    nums = set(re.findall(r"\d+", speed))
    if "100g" in speed or "100000" in nums:
        return "100gbase-x-qsfp28"
    if "40g" in speed or "40000" in nums:
        return "40gbase-x-qsfpp"
    if "25g" in speed or "25000" in nums:
        return "25gbase-x-sfp28"
    if "10g" in speed or "10000" in nums:
        return "10gbase-t"
    return "1000base-t"


def _normalise_mac(mac: str) -> str:
    """Normalise a MAC address to ``AA:BB:CC:DD:EE:FF`` format."""
    if not mac:
        return ""
    digits = mac.replace(":", "").replace("-", "").replace(".", "").upper()
    if len(digits) != 12:
        return ""
    return ":".join(digits[i:i+2] for i in range(0, 12, 2))


def _to_cidr(ip: str, netmask: Optional[str] = None) -> Optional[str]:
    """Return *ip* in CIDR notation.  Falls back to /32 if *netmask* is absent."""
    if not ip:
        return None
    if "/" in ip:
        return ip
    if netmask:
        try:
            import ipaddress
            network = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
            return f"{ip}/{network.prefixlen}"
        except Exception:
            pass
    return f"{ip}/32"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Lenovo XClarity Administrator devices into NetBox.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--env-file",
        metavar="FILE",
        help="Path to a .env file to load (see .env.example). "
             "Defaults to .env in the current directory if present.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without modifying NetBox.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)

    # Load .env first so that the log-level variable is available
    load_env_file(args.env_file)

    log_level = (
        logging.DEBUG
        if args.verbose
        else getattr(logging, _env("COLLECTOR_LOG_LEVEL", "INFO").upper(), logging.INFO)
    )
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    _validate_env()

    if args.dry_run:
        logger.info("*** DRY-RUN mode — no changes will be written to NetBox ***")

    # Build XClarity client
    xc = XClarityClient(
        host=_env("XCLARITY_HOST"),
        username=_env("XCLARITY_USERNAME"),
        password=_env("XCLARITY_PASSWORD"),
        port=int(_env("XCLARITY_PORT", "443")),
        verify_ssl=_env("XCLARITY_VERIFY_SSL", "true").lower() not in ("false", "0", "no"),
        timeout=int(_env("XCLARITY_TIMEOUT", "30")),
    )

    # Build NetBox client
    nb = pynetbox2.api(
        url=_env("NETBOX_URL"),
        token=_env("NETBOX_TOKEN"),
        rate_limit_per_second=float(_env("NETBOX_RATE_LIMIT", "5")),
        retry_attempts=int(_env("NETBOX_RETRY_ATTEMPTS", "3")),
    )

    nb_sync = NetBoxSync(nb, dry_run=args.dry_run)
    collector = Collector(xc, nb_sync)
    collector.run()


if __name__ == "__main__":
    main()
