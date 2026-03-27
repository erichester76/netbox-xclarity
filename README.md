# netbox-xclarity

**netbox-xclarity** is a Python-based collector that synchronizes hardware inventory from a [Lenovo XClarity Administrator](https://www.lenovo.com/us/en/data-center/software/systems-management/xclarity/) instance into [NetBox](https://netbox.dev/). It runs as a lightweight, containerized long-polling loop and keeps NetBox's DCIM inventory up to date without manual data entry.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
  - [XClarity Administrator](#xclarity-administrator)
  - [NetBox Connection](#netbox-connection)
  - [Device Roles](#device-roles)
  - [NetBox API Behaviour](#netbox-api-behaviour)
  - [Cache Backends](#cache-backends)
  - [Collector Behaviour](#collector-behaviour)
  - [Regex Transformations](#regex-transformations)
- [Running with Docker Compose](#running-with-docker-compose)
- [Running Standalone](#running-standalone)
- [What Gets Synced](#what-gets-synced)
  - [Device Categories](#device-categories)
  - [Network Interfaces & IP Addresses](#network-interfaces--ip-addresses)
  - [Inventory Items vs NetBox Modules](#inventory-items-vs-netbox-modules)
- [NetBox Permissions](#netbox-permissions)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## Overview

| Feature | Details |
|---|---|
| **Source** | Lenovo XClarity Administrator REST API |
| **Target** | NetBox 3.x / 4.x (REST API or Diode write-through) |
| **Device types** | Servers (nodes), blade chassis, top-of-rack switches, storage arrays |
| **Inventory** | CPUs, memory DIMMs, disks, PCIe adapters, power supplies, fans, backplanes |
| **Networking** | Ethernet/FC/IB interfaces, management IP addresses |
| **Inventory mode** | Traditional `InventoryItem` **or** NetBox 4.x `Module`/`ModuleBay` objects |
| **Deployment** | Docker container — runs on a configurable polling interval |

---

## How It Works

```
Lenovo XClarity Administrator
        │  REST API  (nodes / chassis / switches / storage)
        ▼
   XClarityClient
        │  JSON payloads
        ▼
   Collector  ──────────────────────────────────────────────────────────────┐
        │  upsert devices, interfaces, inventory / modules, IPs             │
        ▼                                                                    │
   NetBoxSync  (prerequisite objects: site, rack, role, manufacturer …)     │
        │                                                                    │
        ▼                                                                    │
   NetBoxExtendedClient  (caching · rate-limiting · retry · diff)           │
        │                                                                    │
        ├─── PynetboxAdapter  ─── pynetbox  ─── NetBox REST API             │
        └─── DiodeAdapter     ─── Diode SDK ─── NetBox (write-through) ◄───┘
```

The collector is **idempotent**: on every run it compares what XClarity reports with what is already in NetBox and only creates or updates objects that have changed.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 + |
| Docker (optional) | 20.10 + |
| NetBox | 3.5 + (Modules require 4.0 +) |
| Lenovo XClarity Administrator | 4.x + |

Python package dependencies (installed automatically):

```
pynetbox>=7.0.0
deepdiff>=6.0.0
requests>=2.28.0
python-dotenv>=1.0.0
redis
netboxlabs-diode-sdk
```

---

## Quick Start

### 1 — Copy and edit the environment file

```bash
cp .env.example .env
# Fill in at minimum:
#   XCLARITY_HOST, XCLARITY_USERNAME, XCLARITY_PASSWORD
#   NETBOX_URL, NETBOX_TOKEN
```

### 2 — Run with Docker Compose

```bash
docker compose up -d
```

Logs are streamed to stdout; the container restarts automatically after each
collection cycle (default pause between runs: 30 seconds).

### 3 — Or run directly (without Docker)

```bash
pip install -r requirements.txt
python collector.py --env-file .env
```

---

## Configuration Reference

All settings are read from environment variables. Variables can be placed in a
`.env` file (copy `.env.example` as a starting point) **or** exported in the
shell environment. Shell variables always take precedence over the `.env` file.

### XClarity Administrator

| Variable | Required | Default | Description |
|---|---|---|---|
| `XCLARITY_HOST` | ✅ | — | Hostname or IP address of the XClarity Administrator instance |
| `XCLARITY_PORT` | | `443` | HTTPS port for the XClarity REST API |
| `XCLARITY_USERNAME` | ✅ | — | XClarity Administrator username |
| `XCLARITY_PASSWORD` | ✅ | — | XClarity Administrator password |
| `XCLARITY_VERIFY_SSL` | | `true` | Set to `false` only in lab environments with self-signed certificates |
| `XCLARITY_TIMEOUT` | | `30` | Request timeout in seconds |

### NetBox Connection

| Variable | Required | Default | Description |
|---|---|---|---|
| `NETBOX_URL` | ✅ | — | Full URL of the NetBox instance — no trailing slash (e.g. `https://netbox.example.com`) |
| `NETBOX_TOKEN` | ✅ | — | NetBox API token with read/write access |

### Device Roles

These slugs are looked up (and created if absent) in NetBox automatically.

| Variable | Default | Description |
|---|---|---|
| `NETBOX_DEFAULT_SITE` | `default` | Site slug assigned to devices that carry no location data in XClarity |
| `NETBOX_SERVER_ROLE` | `server` | Device role slug for compute servers |
| `NETBOX_CHASSIS_ROLE` | `chassis` | Device role slug for blade chassis |
| `NETBOX_SWITCH_ROLE` | `switch` | Device role slug for managed switches |
| `NETBOX_STORAGE_ROLE` | `storage` | Device role slug for storage arrays |

### NetBox API Behaviour

| Variable | Default | Description |
|---|---|---|
| `NETBOX_RATE_LIMIT` | `5` | Maximum NetBox API calls per second; `0` disables throttling |
| `NETBOX_RETRY_ATTEMPTS` | `3` | How many times to retry a failed API call (exponential back-off with jitter) |

### Cache Backends

The collector caches NetBox API responses to avoid redundant round-trips during a
single sync run. Three backends are supported:

| Variable | Default | Description |
|---|---|---|
| `NETBOX_CACHE_BACKEND` | `sqlite` | Cache backend: `none`, `sqlite`, or `redis` |
| `NETBOX_CACHE_TTL` | `300` | Cache entry lifetime in seconds |
| `NETBOX_CACHE_SQLITE_PATH` | `.nbx_cache.sqlite3` | Path to the SQLite database file (used when `NETBOX_CACHE_BACKEND=sqlite`) |
| `NETBOX_CACHE_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL (used when `NETBOX_CACHE_BACKEND=redis`) |

**Choosing a backend:**

- `none` — no caching; every API lookup hits the network. Useful for debugging.
- `sqlite` *(default)* — single-file local cache; no external dependencies, ideal
  for single-host deployments.
- `redis` — shared cache; useful when multiple collector instances run in parallel
  or when you want the cache to survive container restarts.

### Collector Behaviour

| Variable | Default | Description |
|---|---|---|
| `COLLECTOR_CATEGORIES` | `nodes,chassis,switches,storage` | Comma-separated list of device categories to collect |
| `COLLECTOR_SYNC_INTERFACES` | `true` | Sync network interfaces and IP addresses |
| `COLLECTOR_SYNC_INVENTORY` | `true` | Sync hardware inventory (CPUs, memory, disks, PSUs, fans) |
| `COLLECTOR_USE_MODULES` | `false` | Use NetBox **Modules** instead of Inventory Items — requires NetBox 4.0 + |
| `COLLECTOR_LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

### Regex Transformations

Optional Python `re.sub`-style transformations applied to field values before
they are written to NetBox. Leave the `_REGEX` variable empty to disable.

| Variables | Applied to |
|---|---|
| `COLLECTOR_HOSTNAME_REGEX` / `COLLECTOR_HOSTNAME_REPLACE` | Device hostname |
| `COLLECTOR_LOCATION_REGEX` / `COLLECTOR_LOCATION_REPLACE` | XClarity `location.location` → NetBox **site** name |
| `COLLECTOR_ROOM_REGEX` / `COLLECTOR_ROOM_REPLACE` | XClarity `location.room` → NetBox **location** name |

**Example** — strip a domain suffix from all hostnames:

```dotenv
COLLECTOR_HOSTNAME_REGEX=\.example\.com$
COLLECTOR_HOSTNAME_REPLACE=
```

---

## Running with Docker Compose

```bash
# Build and start in the background
docker compose up -d --build

# Tail logs
docker compose logs -f xclarity-sync

# Stop
docker compose down
```

The `docker-compose.yml` mounts `collector.py` and `pynetbox2.py` as volumes so
you can edit them without rebuilding the image. After each collection cycle the
container sleeps for `RESTART_DELAY` seconds (default: `30`) before running
again.

To change the polling interval, set `RESTART_DELAY` in your `.env` file:

```dotenv
RESTART_DELAY=300   # run every 5 minutes
```

The `o11y` Docker network referenced in `docker-compose.yml` is expected to be
an externally managed network. Create it once if it does not already exist:

```bash
docker network create o11y
```

---

## Running Standalone

Install dependencies and run directly:

```bash
pip install -r requirements.txt

# Use a .env file
python collector.py --env-file .env

# Or rely on shell environment variables
python collector.py

# Dry run — discover and log changes without writing to NetBox
python collector.py --dry-run

# Verbose output
python collector.py --verbose
```

### CLI Options

| Flag | Description |
|---|---|
| `--env-file PATH` | Path to the `.env` file to load (default: `.env` in the working directory) |
| `--dry-run` | Discover changes and log them without modifying NetBox |
| `--verbose` | Force `DEBUG`-level logging regardless of `COLLECTOR_LOG_LEVEL` |

---

## What Gets Synced

### Device Categories

#### Servers / Nodes (`nodes`)

Compute servers managed by XClarity. Each node is synced as a NetBox **Device**
with:

- Serial number, model name, firmware version (platform)
- Rack position (unit, height) when XClarity location data is available
- Management IP address (tagged as primary out-of-band IP)
- All enabled network interfaces
- Full hardware inventory (CPUs, DIMMs, disks, PCIe adapters, PSUs, fans,
  backplanes)

#### Blade Chassis (`chassis`)

Blade chassis appear as parent **Devices**. Blade nodes are linked to their
chassis via a **Virtual Chassis** relationship in NetBox.

#### Switches (`switches`)

Top-of-rack and in-chassis switches managed by XClarity. Synced as Devices with
interfaces and management IPs.

#### Storage Arrays (`storage`)

Storage subsystems (DAS/SAN) appear as Devices with their disk inventory.

---

### Network Interfaces & IP Addresses

For each device the collector processes:

1. **PCI add-in adapters** — ports discovered via `portInfo.physicalPorts` (new
   XClarity API style) or the legacy `adapterList`/`adapters`/`networkAdapters`
   fields.
2. **Management / LOM interfaces** — on-board LAN-on-Motherboard and ILOM
   (iDRAC/iLO-equivalent) ports are tagged appropriately.
3. **Out-of-band management IP** — the primary XClarity management IP is written
   as the device's OOB primary IP in NetBox IPAM.

Interface speed and duplex are inferred from XClarity speed fields; the NetBox
interface type (1G copper, 10G SFP+, 25G SFP28, etc.) is selected automatically.

---

### Inventory Items vs NetBox Modules

The collector supports two mutually exclusive modes for hardware sub-components:

#### Traditional Inventory Items (`COLLECTOR_USE_MODULES=false`, default)

Each component (CPU, DIMM, disk, adapter, PSU, fan, backplane) is created as a
flat **InventoryItem** on the parent device. Roles such as `cpu`, `memory`,
`disk`, `psu`, `fan` are auto-created in NetBox.

This mode works with **all NetBox versions** (3.x and 4.x).

#### NetBox Modules (`COLLECTOR_USE_MODULES=true`, requires NetBox 4.0 +)

Components are represented using the structured Modules API:

1. **ModuleBayTemplate** — a slot definition on the Device Type (e.g. `CPU
   Socket 1`, `DIMM Slot A1`, `PCIe Slot 3`).
2. **ModuleType** — a type record for the installed component (e.g. the specific
   CPU model).
3. **Module** — an instance of a ModuleType installed in a specific bay on the
   device, carrying the serial number and custom attributes (cores, speed, etc.).

Modules preserve physical slot/bay context that flat InventoryItems cannot
express, and interfaces can be linked back to the module they belong to.

---

## NetBox Permissions

The API token supplied via `NETBOX_TOKEN` requires **read and write** access to
the following NetBox resources:

| App | Resources |
|---|---|
| `dcim` | sites, locations, racks, manufacturers, device_types, device_roles, platforms, devices, interfaces, inventory_item_roles, inventory_items, modules, module_bays, module_bay_templates, module_types, power_ports |
| `ipam` | ip_addresses |

The easiest approach is to create a dedicated **NetBox user** with a custom
permission that grants `view`, `add`, `change`, and `delete` on all `dcim` and
`ipam` objects, and then generate an API token for that user.

---

## Troubleshooting

### Collector exits immediately / no devices synced

- Verify `XCLARITY_HOST`, `XCLARITY_USERNAME`, and `XCLARITY_PASSWORD` are correct.
- Set `XCLARITY_VERIFY_SSL=false` temporarily if the XClarity instance uses a
  self-signed certificate.
- Set `COLLECTOR_LOG_LEVEL=DEBUG` to see the raw API responses.

### NetBox API errors (403 / 404)

- Check that the token has the required permissions (see [NetBox Permissions](#netbox-permissions)).
- Confirm `NETBOX_URL` does not include a trailing slash.

### Devices appear at the wrong site

- XClarity location fields (`location.location` and `location.room`) are used to
  derive the NetBox site and location. If these fields are absent or do not match
  existing NetBox site slugs, the device falls back to `NETBOX_DEFAULT_SITE`.
- Use the `COLLECTOR_LOCATION_REGEX` / `COLLECTOR_LOCATION_REPLACE` variables to
  normalise location strings.

### Duplicate or missing interfaces

- Enable `COLLECTOR_LOG_LEVEL=DEBUG` to see which adapters and ports are being
  discovered.
- Old-style XClarity payloads (pre-4.x firmware) use different field names.  The
  collector handles both; if ports are still missing, open an issue and include
  the sanitised JSON from `/nodes/{uuid}` in XClarity.

### Cache is stale / objects not updating

- Delete the SQLite cache file (`rm .nbx_cache.sqlite3`) and re-run.
- Reduce `NETBOX_CACHE_TTL` to `0` to disable TTL-based caching during
  debugging.
- Switch to `NETBOX_CACHE_BACKEND=none` to bypass the cache entirely.

### Modules not appearing (NetBox 4.x)

- Confirm `COLLECTOR_USE_MODULES=true` is set.
- Verify the NetBox instance is running **4.0 or later** (the
  `dcim.module_bay_templates` endpoint must exist).
- Check that `COLLECTOR_SYNC_INVENTORY=true`.

---

## Contributing

1. Fork the repository and create a feature branch.
2. Make your changes; follow the existing code style.
3. Test against a real (or sandbox) XClarity + NetBox environment with
   `--dry-run` before committing.
4. Open a Pull Request describing what you changed and why.

Issues and pull requests are welcome.
