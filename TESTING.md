# Testing Guide for netbox-xclarity

This document explains how the test suite is structured, how to run it, and how to extend it as the codebase evolves. It is intended as a reference for both human contributors and automated agents working in this repository.

---

## Quick Start

```bash
# 1. Install runtime dependencies
pip install -r requirements.txt

# 2. Install test-only dependencies
pip install -r requirements-dev.txt

# 3. Run the full test suite
pytest
```

All tests are self-contained and require **no** running XClarity or NetBox instance.

---

## Directory Structure

```
netbox-xclarity/
├── collector.py            # Main XClarity → NetBox sync script
├── pynetbox2.py            # Enhanced NetBox API client library
├── requirements.txt        # Runtime dependencies
├── requirements-dev.txt    # Test-only dependencies (pytest, requests-mock, …)
├── pytest.ini              # pytest configuration
└── tests/
    ├── __init__.py
    ├── conftest.py                  # Shared fixtures (sample API payloads)
    ├── test_collector_utils.py     # Pure utility functions from collector.py
    ├── test_xclarity_client.py     # XClarityClient HTTP layer (requests_mock)
    └── test_pynetbox2_utils.py     # FK normalisation, RateLimiter, cache backends
```

---

## Test Files and What They Cover

### `tests/test_collector_utils.py`

Unit tests for every pure (no I/O) helper function exported by `collector.py`:

| Test Class | Function under test |
|---|---|
| `TestSlugify` | `_slugify()` – NetBox slug generation |
| `TestApplyRegex` | `_apply_regex()` – regex transform with error handling |
| `TestBuildModelName` | `_build_model_name()` – XClarity device → type name |
| `TestPortType` | `_port_type()` – string-speed → NetBox interface type |
| `TestPortTypeGbps` | `_port_type_gbps()` – integer Gbps → NetBox interface type |
| `TestNormaliseMac` | `_normalise_mac()` – MAC address normalisation |
| `TestToCidr` | `_to_cidr()` – IP + netmask → CIDR notation |
| `TestPsuPlugType` | `_psu_plug_type()` – IEC 60320 C14/C20 selection |
| `TestCpuAttributes` | `_cpu_attributes()` – CPU component attributes |
| `TestMemoryAttributes` | `_memory_attributes()` – DIMM component attributes |
| `TestStorageAttributes` | `_storage_attributes()` – disk drive attributes |
| `TestFanAttributes` | `_fan_attributes()` – fan component attributes |
| `TestPsuAttributes` | `_psu_attributes()` – PSU component attributes |
| `TestExpansionCardAttributes` | `_expansion_card_attributes()` – PCIe card attributes |

### `tests/test_xclarity_client.py`

Tests for `XClarityClient` in `collector.py`. All HTTP requests are intercepted with the `requests_mock` pytest plugin — no real server is required.

Covers:
- `get_nodes()`, `get_chassis()`, `get_switches()`, `get_storage()` — both the list-wrapped (`{"nodeList": […]}`) and bare-list (`[…]`) response formats
- `get_node_details(uuid)`, `get_chassis_details(uuid)` — detail payloads
- HTTP error propagation (`requests.HTTPError`)
- URL construction and session authentication

### `tests/test_pynetbox2_utils.py`

Tests for non-I/O components of `pynetbox2.py`:

| Test Class | Component under test |
|---|---|
| `TestNormalizeFkFields` | `normalize_fk_fields()` – FK field normalisation for GET vs POST/PATCH |
| `TestRateLimiter` | `RateLimiter` – token-bucket throttle |
| `TestNullCacheBackend` | `NullCacheBackend` – no-op cache |
| `TestSQLiteCacheBackend` | `SQLiteCacheBackend` – SQLite TTL cache |

### `tests/conftest.py`

Shared pytest fixtures used across multiple test files:

| Fixture | Description |
|---|---|
| `sample_cpu` | Minimal XClarity CPU component dict |
| `sample_dimm` | Minimal XClarity memory DIMM dict |
| `sample_drive` | Minimal XClarity disk drive dict (capacity in bytes) |
| `sample_psu` | Minimal XClarity PSU dict |
| `sample_fan` | Minimal XClarity fan dict |
| `sample_expansion_card` | Minimal XClarity PCIe adapter dict |
| `sample_node` | Minimal XClarity node (server) payload |

---

## Running Tests

### Run everything

```bash
pytest
```

### Run a specific file

```bash
pytest tests/test_collector_utils.py
```

### Run a specific test class or function

```bash
pytest tests/test_collector_utils.py::TestSlugify
pytest tests/test_collector_utils.py::TestSlugify::test_basic_lowercase
```

### Run with increased verbosity

```bash
pytest -v
```

### Run with coverage (requires `pytest-cov`)

```bash
pip install pytest-cov
pytest --cov=collector --cov=pynetbox2 --cov-report=term-missing
```

---

## Adding New Tests

### For a new utility function in `collector.py`

1. Add a new `class Test<FunctionName>` to `tests/test_collector_utils.py`.
2. Import the function at the top of the file (alongside the existing imports).
3. Write at least one test for the happy path, one for edge cases (empty input, None, invalid type), and one for the boundary condition if applicable.

### For a new `XClarityClient` endpoint

1. Add a new `class TestGet<ResourceName>` to `tests/test_xclarity_client.py`.
2. Use the `requests_mock` fixture to register the expected URL and response body.
3. Verify both the list-wrapped `{"<resource>List": […]}` and bare `[…]` response formats, plus HTTP error propagation.

### For a new `pynetbox2.py` component

1. Add a new `class Test<ComponentName>` to `tests/test_pynetbox2_utils.py`.
2. If the component requires I/O (database, network), use `tmp_path` (for SQLite) or `unittest.mock.patch` / `pytest-mock` to avoid real connections.

### For `NetBoxSync` or `Collector` (integration-level)

Create a new file `tests/test_netboxsync.py` or `tests/test_collector.py`. These require mocking the entire `pynetbox2.NetBoxAPI` object. Use `pytest-mock`'s `mocker.MagicMock()` to stub the `nb.upsert`, `nb.get`, and `nb.list` methods and assert the correct payloads are passed through.

Example skeleton:

```python
from unittest.mock import MagicMock
from collector import NetBoxSync

def test_ensure_manufacturer_caches_result():
    nb = MagicMock()
    nb.upsert.return_value = {"id": 1, "name": "Lenovo", "slug": "lenovo"}
    sync = NetBoxSync(nb)
    mfr_id = sync.ensure_manufacturer()
    assert mfr_id == 1
    # Second call must use cache, not hit the API again
    sync.ensure_manufacturer()
    assert nb.upsert.call_count == 1
```

---

## Dependencies

Runtime (from `requirements.txt`):
- `pynetbox` – NetBox REST API client
- `deepdiff` – diff-based change detection for upsert
- `requests` – HTTP client
- `python-dotenv` – `.env` file loading
- `redis` – optional Redis cache backend
- `netboxlabs-diode-sdk` – optional Diode write backend

Test-only (from `requirements-dev.txt`):
- `pytest` – test runner
- `pytest-mock` – mock/spy fixtures via `mocker`
- `requests-mock` – HTTP request interception for `requests` library

---

## Design Principles

1. **No real infrastructure required.** All network calls (XClarity REST, NetBox REST) are mocked. SQLite tests use `tmp_path` fixtures to create throwaway databases.

2. **Each test is independent.** Tests do not share state; fixtures provide fresh objects for each test function.

3. **Test the contract, not the implementation.** Tests assert on return values and side effects (calls made, exceptions raised), not on internal implementation details like variable names.

4. **Edge cases over happy paths.** Every function has tests for empty/None/invalid inputs in addition to the normal usage path.
