"""Unit tests for pure utility functions defined in collector.py.

These tests exercise every helper that has no external I/O dependency so they
run quickly and without any network or database access.
"""

from __future__ import annotations

import pytest

# collector.py is a script, not a package, so import its helpers directly
# after ensuring the project root is on sys.path (handled by conftest.py).
from collector import (
    _apply_regex,
    _build_model_name,
    _cpu_attributes,
    _expansion_card_attributes,
    _fan_attributes,
    _memory_attributes,
    _normalise_mac,
    _port_type,
    _port_type_gbps,
    _psu_attributes,
    _psu_plug_type,
    _slugify,
    _storage_attributes,
    _to_cidr,
    _CAPACITY_BYTES_THRESHOLD,
    _PSU_C20_WATTAGE_THRESHOLD,
)


# ===========================================================================
# _slugify
# ===========================================================================

class TestSlugify:
    def test_basic_lowercase(self):
        assert _slugify("Hello World") == "hello-world"

    def test_underscores_become_hyphens(self):
        assert _slugify("foo_bar_baz") == "foo-bar-baz"

    def test_special_chars_removed(self):
        assert _slugify("foo! @bar#") == "foo-bar"

    def test_multiple_spaces_collapsed(self):
        assert _slugify("foo   bar") == "foo-bar"

    def test_leading_trailing_hyphens_stripped(self):
        assert _slugify("-foo-") == "foo"

    def test_already_slug(self):
        assert _slugify("already-a-slug") == "already-a-slug"

    def test_max_length_100(self):
        long_value = "a" * 200
        result = _slugify(long_value)
        assert len(result) <= 100

    def test_mixed_case(self):
        assert _slugify("ThinkSystem SR650") == "thinksystem-sr650"

    def test_numbers_preserved(self):
        assert _slugify("7X06CTO1WW") == "7x06cto1ww"

    def test_empty_string(self):
        assert _slugify("") == ""


# ===========================================================================
# _apply_regex
# ===========================================================================

class TestApplyRegex:
    def test_basic_substitution(self):
        assert _apply_regex("server-01.example.com", r"\.example\.com$", "") == "server-01"

    def test_empty_pattern_returns_value_unchanged(self):
        assert _apply_regex("server-01", "", "x") == "server-01"

    def test_empty_value_returns_unchanged(self):
        assert _apply_regex("", r"\d+", "X") == ""

    def test_invalid_regex_returns_value_unchanged(self):
        # An unbalanced bracket is an invalid regex
        result = _apply_regex("server-01", r"[invalid", "x")
        assert result == "server-01"

    def test_replacement_applied(self):
        assert _apply_regex("DC-Room-3", r"DC-Room-(\d+)", r"Room\1") == "Room3"

    def test_no_match_returns_value_unchanged(self):
        assert _apply_regex("server-01", r"chassis-\d+", "") == "server-01"


# ===========================================================================
# _build_model_name
# ===========================================================================

class TestBuildModelName:
    def test_full_fields(self):
        device = {
            "productName": "ThinkSystem SR650",
            "machineType": "7X06",
            "model": "CTO1WW",
        }
        assert _build_model_name(device) == "ThinkSystem SR650 -[7X06CTO1WW]-"

    def test_product_name_only(self):
        device = {"productName": "ThinkSystem SR650", "machineType": "", "model": ""}
        assert _build_model_name(device) == "ThinkSystem SR650"

    def test_machine_type_and_model_only(self):
        device = {"productName": "", "machineType": "7X06", "model": "CTO1WW"}
        assert _build_model_name(device) == "-[7X06CTO1WW]-"

    def test_machine_type_only(self):
        device = {"productName": "", "machineType": "7X06", "model": ""}
        assert _build_model_name(device) == "-[7X06]-"

    def test_empty_device(self):
        assert _build_model_name({}) == ""

    def test_manufacturer_field_ignored(self):
        # manufacturer is present in the dict but should not appear in the name
        device = {
            "manufacturer": "Lenovo",
            "productName": "ThinkSystem SR650",
            "machineType": "7X06",
            "model": "",
        }
        assert "Lenovo" not in _build_model_name(device)


# ===========================================================================
# _port_type
# ===========================================================================

class TestPortType:
    def test_100g(self):
        assert _port_type({"speed": "100g"}) == "100gbase-x-qsfp28"

    def test_100000_numeric(self):
        assert _port_type({"speed": "100000"}) == "100gbase-x-qsfp28"

    def test_40g(self):
        assert _port_type({"speed": "40g"}) == "40gbase-x-qsfpp"

    def test_40000_numeric(self):
        assert _port_type({"portSpeed": "40000"}) == "40gbase-x-qsfpp"

    def test_25g(self):
        assert _port_type({"speed": "25g"}) == "25gbase-x-sfp28"

    def test_10g(self):
        assert _port_type({"speed": "10g"}) == "10gbase-t"

    def test_10000_numeric(self):
        assert _port_type({"speed": "10000"}) == "10gbase-t"

    def test_default_1g(self):
        assert _port_type({"speed": "1g"}) == "1000base-t"

    def test_empty_speed_defaults_1g(self):
        assert _port_type({}) == "1000base-t"

    def test_no_false_positive_10000_vs_100000(self):
        # "10000" must not match "100000" branch
        result = _port_type({"speed": "10000"})
        assert result == "10gbase-t"


# ===========================================================================
# _port_type_gbps
# ===========================================================================

class TestPortTypeGbps:
    def test_100gbps(self):
        assert _port_type_gbps(100) == "100gbase-x-qsfp28"

    def test_above_100gbps(self):
        assert _port_type_gbps(400) == "100gbase-x-qsfp28"

    def test_40gbps(self):
        assert _port_type_gbps(40) == "40gbase-x-qsfpp"

    def test_25gbps(self):
        assert _port_type_gbps(25) == "25gbase-x-sfp28"

    def test_10gbps(self):
        assert _port_type_gbps(10) == "10gbase-t"

    def test_1gbps(self):
        assert _port_type_gbps(1) == "1000base-t"

    def test_none_defaults_1g(self):
        assert _port_type_gbps(None) == "1000base-t"

    def test_string_int(self):
        assert _port_type_gbps("25") == "25gbase-x-sfp28"

    def test_invalid_string_defaults_1g(self):
        assert _port_type_gbps("fast") == "1000base-t"


# ===========================================================================
# _normalise_mac
# ===========================================================================

class TestNormaliseMac:
    def test_colon_separated(self):
        assert _normalise_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"

    def test_dash_separated(self):
        assert _normalise_mac("AA-BB-CC-DD-EE-FF") == "AA:BB:CC:DD:EE:FF"

    def test_dot_separated(self):
        assert _normalise_mac("aabb.ccdd.eeff") == "AA:BB:CC:DD:EE:FF"

    def test_no_separator(self):
        assert _normalise_mac("AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF"

    def test_empty_string_returns_empty(self):
        assert _normalise_mac("") == ""

    def test_too_short_returns_empty(self):
        assert _normalise_mac("AABBCC") == ""

    def test_too_long_returns_empty(self):
        assert _normalise_mac("AABBCCDDEEFF00") == ""

    def test_uppercase_preserved(self):
        result = _normalise_mac("aa:bb:cc:dd:ee:ff")
        assert result == result.upper()


# ===========================================================================
# _to_cidr
# ===========================================================================

class TestToCidr:
    def test_already_cidr(self):
        assert _to_cidr("192.168.1.1/24") == "192.168.1.1/24"

    def test_with_netmask(self):
        result = _to_cidr("10.0.0.5", "255.255.255.0")
        assert result == "10.0.0.5/24"

    def test_without_netmask_defaults_32(self):
        assert _to_cidr("10.0.0.1") == "10.0.0.1/32"

    def test_empty_ip_returns_none(self):
        assert _to_cidr("") is None

    def test_none_ip_returns_none(self):
        assert _to_cidr(None) is None  # type: ignore[arg-type]

    def test_invalid_netmask_falls_back_to_32(self):
        result = _to_cidr("10.0.0.1", "not-a-mask")
        assert result == "10.0.0.1/32"

    def test_slash_30(self):
        result = _to_cidr("192.168.0.1", "255.255.255.252")
        assert result == "192.168.0.1/30"


# ===========================================================================
# _psu_plug_type
# ===========================================================================

class TestPsuPlugType:
    def test_low_wattage_returns_c14(self):
        assert _psu_plug_type({"outputWatts": 750}) == "iec-60320-c14"

    def test_exactly_at_threshold_returns_c14(self):
        assert _psu_plug_type({"outputWatts": _PSU_C20_WATTAGE_THRESHOLD}) == "iec-60320-c14"

    def test_above_threshold_returns_c20(self):
        assert _psu_plug_type({"outputWatts": _PSU_C20_WATTAGE_THRESHOLD + 1}) == "iec-60320-c20"

    def test_high_wattage_returns_c20(self):
        assert _psu_plug_type({"outputWatts": 2400}) == "iec-60320-c20"

    def test_no_wattage_returns_c14(self):
        assert _psu_plug_type({}) == "iec-60320-c14"

    def test_power_allocation_fallback(self):
        psu = {"powerAllocation": {"totalOutputPower": 2000}}
        assert _psu_plug_type(psu) == "iec-60320-c20"

    def test_invalid_wattage_value_returns_c14(self):
        assert _psu_plug_type({"outputWatts": "N/A"}) == "iec-60320-c14"


# ===========================================================================
# _cpu_attributes
# ===========================================================================

class TestCpuAttributes:
    def test_full_fields(self, sample_cpu):
        attrs = _cpu_attributes(sample_cpu)
        assert attrs["cores"] == 16
        assert attrs["speed"] == 2.4
        assert attrs["architecture"] == "x86_64"

    def test_empty_dict(self):
        assert _cpu_attributes({}) == {}

    def test_invalid_cores_skipped(self):
        attrs = _cpu_attributes({"cores": "N/A"})
        assert "cores" not in attrs

    def test_architecture_fallback_to_cpu_family(self):
        attrs = _cpu_attributes({"cpuFamily": "x86"})
        assert attrs["architecture"] == "x86"

    def test_architecture_fallback_to_family(self):
        attrs = _cpu_attributes({"family": "ARM"})
        assert attrs["architecture"] == "ARM"

    def test_string_cores_converted_to_int(self):
        attrs = _cpu_attributes({"cores": "8"})
        assert attrs["cores"] == 8


# ===========================================================================
# _memory_attributes
# ===========================================================================

class TestMemoryAttributes:
    def test_full_fields(self, sample_dimm):
        attrs = _memory_attributes(sample_dimm)
        assert attrs["size"] == 32
        assert attrs["class"] == "DDR4"
        assert attrs["data_rate"] == 3200
        assert attrs["ecc"] is True

    def test_ddr5_detected(self):
        attrs = _memory_attributes({"memoryType": "DDR5-4800"})
        assert attrs["class"] == "DDR5"

    def test_ddr3_detected(self):
        attrs = _memory_attributes({"memoryType": "DDR3"})
        assert attrs["class"] == "DDR3"

    def test_unknown_type_no_class(self):
        # "SDRAM" does not contain DDR3/DDR4/DDR5 as a substring
        attrs = _memory_attributes({"memoryType": "SDRAM"})
        assert "class" not in attrs

    def test_capacity_alias_size(self):
        attrs = _memory_attributes({"size": 64})
        assert attrs["size"] == 64

    def test_ecc_via_ecc_key(self):
        attrs = _memory_attributes({"ecc": False})
        assert attrs["ecc"] is False

    def test_empty_dict(self):
        assert _memory_attributes({}) == {}


# ===========================================================================
# _storage_attributes
# ===========================================================================

class TestStorageAttributes:
    def test_ssd_type(self):
        attrs = _storage_attributes({"mediaType": "SSD"}, 480)
        assert attrs["type"] == "SSD"
        assert attrs["size"] == 480

    def test_nvme_type(self):
        attrs = _storage_attributes({"mediaType": "NVMe"}, 960)
        assert attrs["type"] == "NVME"

    def test_nvme_via_nvm(self):
        attrs = _storage_attributes({"mediaType": "NVM"}, 0)
        assert attrs["type"] == "NVME"

    def test_hdd_type(self):
        attrs = _storage_attributes({"mediaType": "HDD"}, 2000)
        assert attrs["type"] == "HD"

    def test_rpm_included(self):
        attrs = _storage_attributes({"rpm": 7200, "mediaType": "HDD"}, 1000)
        assert attrs["speed"] == 7200

    def test_zero_capacity_no_size_key(self):
        attrs = _storage_attributes({}, 0)
        assert "size" not in attrs

    def test_solid_state_matches_ssd(self):
        attrs = _storage_attributes({"mediaType": "SOLID STATE"}, 240)
        assert attrs["type"] == "SSD"

    def test_flash_matches_ssd(self):
        attrs = _storage_attributes({"mediaType": "FLASH"}, 240)
        assert attrs["type"] == "SSD"


# ===========================================================================
# _fan_attributes
# ===========================================================================

class TestFanAttributes:
    def test_speed_key(self, sample_fan):
        attrs = _fan_attributes(sample_fan)
        assert attrs["rpm"] == 3000

    def test_rpm_key_fallback(self):
        attrs = _fan_attributes({"rpm": 4500})
        assert attrs["rpm"] == 4500

    def test_empty_dict(self):
        assert _fan_attributes({}) == {}

    def test_invalid_speed_skipped(self):
        attrs = _fan_attributes({"speed": "N/A"})
        assert "rpm" not in attrs


# ===========================================================================
# _psu_attributes
# ===========================================================================

class TestPsuAttributes:
    def test_ac_input_current(self, sample_psu):
        attrs = _psu_attributes(sample_psu)
        assert attrs["input_current"] == "AC"

    def test_dc_via_input_voltage_is_ac_false(self):
        attrs = _psu_attributes({"inputVoltageIsAC": False})
        assert attrs["input_current"] == "DC"

    def test_dc_via_input_voltage_type(self):
        attrs = _psu_attributes({"inputVoltageType": "DC"})
        assert attrs["input_current"] == "DC"

    def test_input_voltage(self, sample_psu):
        attrs = _psu_attributes(sample_psu)
        assert attrs["input_voltage"] == 220

    def test_wattage(self, sample_psu):
        attrs = _psu_attributes(sample_psu)
        assert attrs["wattage"] == 750

    def test_hot_swappable_true(self, sample_psu):
        attrs = _psu_attributes(sample_psu)
        assert attrs["hot_swappable"] is True

    def test_hot_swappable_alias(self):
        attrs = _psu_attributes({"isHotSwappable": False})
        assert attrs["hot_swappable"] is False

    def test_power_allocation_fallback_for_wattage(self):
        psu = {"powerAllocation": {"totalOutputPower": 1200}}
        attrs = _psu_attributes(psu)
        assert attrs["wattage"] == 1200

    def test_nominal_voltage_fallback(self):
        attrs = _psu_attributes({"nominalVoltage": 110})
        assert attrs["input_voltage"] == 110


# ===========================================================================
# _expansion_card_attributes
# ===========================================================================

class TestExpansionCardAttributes:
    def test_full_fields(self, sample_expansion_card):
        attrs = _expansion_card_attributes(sample_expansion_card)
        assert attrs["bandwidth"] == 16
        assert attrs["connector_type"] == "PCIe x16"

    def test_connector_type_fallback_connector_type(self):
        attrs = _expansion_card_attributes({"connectorType": "PCIe x8"})
        assert attrs["connector_type"] == "PCIe x8"

    def test_connector_type_fallback_slot_type(self):
        attrs = _expansion_card_attributes({"slotType": "PCIe x4"})
        assert attrs["connector_type"] == "PCIe x4"

    def test_empty_dict(self):
        assert _expansion_card_attributes({}) == {}

    def test_invalid_bandwidth_skipped(self):
        attrs = _expansion_card_attributes({"bandwidth": "N/A"})
        assert "bandwidth" not in attrs
