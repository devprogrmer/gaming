# gaming

[![CI](https://github.com/your-org/gaming/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/gaming/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **Note:** `gaming` is only the project name. This is **not** a video game, game engine, or launcher. It is a **network discovery and reachability analysis CLI tool**.

`gaming` discovers IP ranges from public network data sources (RDAP, WHOIS, ASN/BGP, PeeringDB, RIR allocations), filters and normalizes the prefixes, checks reachability, optionally probes ports, optionally performs global reachability checks via check-host.net, and exports structured reports to the console, JSON, or CSV.

It is written for **Python 3.11+** and uses **only the standard library** — no third‑party runtime dependencies — so it runs anywhere Python does. Every network source degrades gracefully: if a live lookup fails or you pass `--offline`, the tool falls back to bundled sample data so the pipeline always produces output.

---

## Features

- **Automatic discovery** from pluggable sources: `rdap`, `whois`, `asn_bgp` (RIPEstat/BGP), `peeringdb`, `rir`.
- **Filtering** by country, ASN, provider, and organization.
- **Focus modes** for **Iranian datacenter** and **foreign datacenter** ranges.
- **Normalization**: validation, de‑duplication with metadata merge, optional prefix collapsing.
- **Reachability**: local alive checks (`ping`/`tcp`/`auto`), optional **port probing**.
- **Global reachability** via check-host.net (opt‑in; only public IPs are submitted).
- **Output** to console, JSON, and CSV. Each record includes: source, ASN, organization, country, prefix, alive status, global reachability, open ports, and notes.
- **Concurrency** via thread pools, **configurable** via TOML + CLI, **logging**, **robust error handling**, and a **test suite**.

---

## Installation

```bash
cd gaming
python -m pip install -e .        # installs the `gaming` console script
# dev extras (pytest):
python -m pip install -e ".[dev]"
```

You can also run it without installing:

```bash
PYTHONPATH=src python -m gaming --help
```

Requires Python **3.11 or newer** (uses `tomllib`).

---

## Quick start

```bash
# List available discovery sources
gaming sources

# Discover ranges offline (bundled sample data), Iranian datacenter focus, as JSON
gaming --offline discover --iran-datacenter --format json

# Foreign datacenter ranges, collapse prefixes, write CSV
gaming --offline discover --foreign-datacenter --collapse --format csv -o foreign.csv

# Check reachability of specific prefixes (local alive + port probe)
gaming check 1.1.1.1 8.8.8.0/24 --ports 80,443 --format console

# Full pipeline: discover -> filter -> normalize -> reachability -> report
gaming --offline run --country IR --ports 80,443 --format json -o report.json
```

> Global checks (`--global`) and non‑offline discovery reach out to the public
> internet. Use them only against infrastructure you are authorized to assess.

---

## CLI reference

Global options (before the subcommand):

| Option | Description |
|---|---|
| `--config, -c PATH` | Path to a TOML config file. |
| `--log-level LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. |
| `--concurrency N` | Max concurrent workers. |
| `--timeout SECONDS` | Per‑operation timeout. |
| `--offline` | Use bundled sample data instead of live network calls. |
| `--quiet, -q` | Log errors only. |
| `--version` | Print version. |

### `gaming sources`
List the available discovery sources.

### `gaming discover`
Discover, filter, and normalize prefixes (no reachability).

Filter flags: `--country IR,DE`, `--asn AS13335,AS24940`, `--provider cloudflare,arvan`, `--org "hetzner"`, `--iran-datacenter`, `--foreign-datacenter`.
Also: `--sources rdap,rir`, `--collapse`, `--format {console,json,csv}`, `--output FILE`.

### `gaming check PREFIX [PREFIX ...]`
Run reachability checks on the given IPs/CIDRs.
Options: `--ports 22,80,443`, `--method {auto,ping,tcp}`, `--global`, `--format`, `--output`.

### `gaming run`
Full pipeline. Accepts all `discover` filter flags plus `--ports`, `--global`, `--no-reachability`, `--collapse`, `--sources`, and output options.

---

## Configuration

Configuration is layered: **built‑in defaults → TOML file → CLI overrides**. See [`gaming.example.toml`](gaming.example.toml) for a fully‑commented template. Load it with:

```bash
gaming --config gaming.example.toml run --format json
```

Sections: `[general]`, `[discovery]`, `[filters]`, `[reachability]`, `[global_check]`.

---

## Output schema

Each result row (console/JSON/CSV) contains:

| Field | Meaning |
|---|---|
| `source` | Discovery source(s) that produced the record (e.g. `rdap+whois`). |
| `asn` | Autonomous System Number in `AS<n>` form. |
| `organization` | Owning organization, when known. |
| `country` | ISO country code. |
| `provider` | Provider hint (lowercased substring‑matchable). |
| `prefix` | Normalized CIDR. |
| `alive` | Local reachability (`true`/`false`/`null`). |
| `global_reachable` | Global reachability via check-host.net (`true`/`false`/`null`). |
| `open_ports` | Ports found open during probing. |
| `notes` | Provenance / diagnostic notes. |

---

## Architecture

```
src/gaming/
├── cli.py               # argparse subcommands (sources/discover/check/run)
├── pipeline.py          # orchestration: discover -> process -> reachability
├── config.py            # TOML loading + layered overrides (tomllib)
├── models.py            # IPRecord, Filters, normalization helpers
├── logging_setup.py     # logging configuration
├── discovery/           # pluggable sources (common Source interface)
│   ├── base.py          #   Source ABC + DiscoveryContext + offline fallback
│   ├── rdap.py  whois.py  asn_bgp.py  peeringdb.py  rir.py
├── processing/
│   ├── normalize.py     # dedup, metadata merge, prefix collapsing
│   └── filters.py       # country/ASN/provider/org + IR/foreign focus
├── reachability/
│   ├── local.py         # ping/tcp/auto alive checks (concurrent)
│   ├── ports.py         # TCP port probing
│   └── global_check.py  # check-host.net integration (public IPs only)
├── reporting/
│   ├── console.py  json_export.py  csv_export.py
└── utils/http.py        # stdlib HTTP with retries/timeouts
```

**Design principles:** modular and extensible (add a source by implementing
`Source` and registering it), dependency‑free, fail‑soft (a single failing
source or host never aborts the run), and fully testable offline via dependency
injection and bundled sample data.

### Extending: add a discovery source

```python
# src/gaming/discovery/mysource.py
from .base import Source
from ..models import IPRecord

class MySource(Source):
    name = "mysource"
    def _discover_online(self):
        ...  # return list[IPRecord]
    def _sample_data(self):
        return [IPRecord(prefix="203.0.113.0/24", source=self.name, country="US")]
```

Register it in `src/gaming/discovery/__init__.py`'s `REGISTRY`.

---

## Testing

```bash
python -m pytest            # or: PYTHONPATH=src python -m pytest
```

The suite is fully offline (no real network calls): sources use bundled sample
data and reachability is monkeypatched. It covers models/normalization,
config, filters (incl. IR/foreign focus), reporting, reachability logic,
pipeline orchestration, and the CLI end‑to‑end.

---

## Development

```bash
python -m pip install -e ".[dev]"   # install with dev tooling
make check                          # lint + tests (the CI gate)
make cov                            # tests with coverage report
make build                          # build sdist + wheel, verify with twine
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow and how to add a
discovery source or output format. Releases are documented in
[CHANGELOG.md](CHANGELOG.md).

### Building a distribution

```bash
python -m build          # produces dist/gaming-<version>.tar.gz and .whl
twine check dist/*       # validate package metadata
pip install dist/gaming-*.whl
```

---

## Responsible use

This tool performs network reconnaissance and reachability testing. Only use it
against networks and hosts you own or are explicitly authorized to assess.
Global checks submit target IPs to a third‑party service (check-host.net).

## License

MIT — see [LICENSE](LICENSE).
