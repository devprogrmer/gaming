# gaming

[![CI](https://github.com/devprogrmer/gaming/actions/workflows/ci.yml/badge.svg)](https://github.com/devprogrmer/gaming/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**[🇬🇧 English](README.md) · [🇮🇷 فارسی](README.fa.md)**

> **Note:** "gaming" is only the project's name. This tool is **not a game, game
> engine, or launcher**. It is a **command-line tool for IP range discovery and
> network reachability analysis**.

A dependency-free CLI for discovering IP ranges from public network sources,
measuring how reachable they are **from Iran and from abroad simultaneously**,
and reporting the result as a console table, JSON, CSV, or a local web
dashboard.

**Python 3.11+ standard library only — no runtime dependencies, ever.**

---

## Table of contents

- [Highlights](#highlights)
- [What the output actually looks like](#what-the-output-actually-looks-like)
- [Quick install](#quick-install)
- [Full installation](#full-installation)
- [Usage](#usage)
  - [Interactive menu](#interactive-menu)
  - [Command line](#command-line)
  - [Web dashboard](#web-dashboard)
  - [Scheduled scans and alerts](#scheduled-scans-and-alerts)
  - [Exhaustive discovery and continuous watch](#exhaustive-discovery-and-continuous-watch)
  - [IP membership lookup](#ip-membership-lookup)
  - [Seed data maintenance](#seed-data-maintenance)
- [How scanning works](#how-scanning-works)
- [Exporting results](#exporting-results)
- [Configuration](#configuration)
- [Requirements](#requirements)
- [Limitations](#limitations)
- [Architecture](#architecture)
- [Testing and development](#testing-and-development)
- [Responsible use](#responsible-use)
- [License](#license)

---

## Highlights

### Bidirectional reachability (Iran + abroad)

The core capability. Every host is probed **twice** — once from the machine
running the scan (in production, an Iranian server) and once from outside Iran
via a third-party vantage point. Combining both answers classifies each host
into one of four verdicts:

| Verdict | Meaning |
|---|---|
| `INTERNATIONAL` | Reachable from Iran **and** from abroad — the useful case. |
| `IRAN_ONLY` | Reachable from inside Iran, but not from abroad. |
| `ABROAD_ONLY` | Reachable from abroad, but not from inside Iran. |
| `UNREACHABLE` | Not reachable from either direction. |

Two abroad-check providers are supported behind one interface: **check-host.net**
(default) and, optionally, **RIPE Atlas**, with a combined `both` mode. A
provider *outage* is reported distinctly from a host being genuinely
unreachable, so "check-host.net is down" never masquerades as "this IP is
blocked".

### Local web dashboard (`gaming web`)

A full dashboard — search, live scanning, history with a trend chart, and
settings — served from the standard library with **no build step, no CDN, and
no webfonts**. It works identically on an air-gapped host. Sessions are
authenticated with generated credentials shown once on first run; optional
self-signed TLS is available via `--tls`.

Any range you can see, you can probe: every row on Search, provider lookup, and
"what's new" has a **Scan** button that sweeps that one CIDR, and Live Scan
accepts a typed or pasted CIDR directly.

### Find a provider by name, even one nobody seeded

`gaming discover --provider-name "<org>"` asks the registries directly — RDAP
entity search against ARIN and RIPE — instead of matching against the bundled
seed list. A real company nobody has added to `providers.toml` is still found,
with its CIDRs, ASN, and country. A name matching several organizations returns
all of them; a name matching nothing says so plainly. The same lookup backs the
CLI flag, the menu option, and the dashboard panel.

The existing `--provider` flag is unchanged and still searches the seed list.

### "What's new since your last visit"

The 24/7 watcher (`gaming watch`) diffs each cycle's discoveries against what it
already knew and keeps a durable record of the genuinely-new ranges. Come back
after a week and the menu, `gaming watch --whats-new`, and the dashboard will
each tell you what appeared while you were away — with ASN, organization, and
country.

Last-visited is tracked **per surface**, so reading the notice in the terminal
does not clear it in the browser. Reading never acknowledges: the marker moves
only once you have actually looked, and only as far as what you were shown.

### Scheduled monitoring and change alerts

`gaming schedule` re-runs a saved scope on an interval, appending each run to
history so the dashboard's trend chart keeps filling without manual work. After
each run it diffs verdicts against the previous scan and can fire an optional
webhook when a host's verdict changes — turning a one-shot scan into ongoing
monitoring.

### Everything else

- **Interactive menu** (`gaming menu`) for live IP health checking without
  learning any flags.
- **Two ready-made workflows**: Iranian and foreign ranges, each with a
  bundled, editable CIDR list.
- **Live-IP discovery** with a fast probe, upgradeable in place to a full health
  scan.
- **Cross-platform latency and packet-loss measurement** — no `fping`, `tail`,
  or `watch` required.
- **Optional common-port scanning** (TCP connect), fail-soft and independent of
  the main scan.
- **Simple Check-Host-style health grading**: GOOD / MEDIUM / BAD with
  configurable thresholds.
- **Persistent history** in a local SQLite database that survives between runs.
- **Seed freshness validation** (`gaming validate-seed`) with a `last_validated`
  marker.
- **Fail-soft throughout**: one dead source or unresponsive host never aborts a
  run.

---

## What the output actually looks like

### Terminal scan results

A completed scan prints an aligned table. On a colour-capable terminal the
HEALTH and WHITELIST columns are colour-coded (green / yellow / red); piped or
redirected output degrades to exactly the same layout in plain ASCII:

```
HOST            HEALTH  AVG(ms)  LOSS  RECV/SENT  ABROAD      WHITELIST      PORTS
--------------  ------  -------  ----  ---------  ----------  -------------  ------
185.143.232.14  GOOD       18.4    0%        4/4  OK (5/5)    INTERNATIONAL  80,443
185.143.232.51  GOOD       42.7    0%        4/4  OK (4/5)    INTERNATIONAL  443
77.36.164.9     MEDIUM    128.3   12%        3/4  FAIL (0/5)  IRAN_ONLY      -
2.144.12.88     BAD           -  100%        0/4  FAIL (0/5)  UNREACHABLE    -

Total: 4   GOOD: 2   MEDIUM: 1   BAD: 1
INTERNATIONAL: 2   IRAN_ONLY: 1   ABROAD_ONLY: 0   UNREACHABLE: 1
```

The `ABROAD` column shows how many external vantage points answered
(`OK (4/5)`), or `unavailable` when the abroad provider itself is down —
distinct from `FAIL`, which means the host really did not answer.

While a scan runs, a live progress bar shows running verdict tallies:

```
Scanning [########################------] 204/256 ( 79.7%)  G:141 M:38 B:25
```

### Web dashboard

`gaming web` serves a dark-mode dashboard with a persistent sidebar. The main
pages:

```
┌────────────┬──────────────────────────────────────────────────────────┐
│ gaming     │  Live Scan                            ● connected        │
│ dashboard  ├──────────────────────────────────────────────────────────┤
│            │  Live scan                                               │
│ Overview   │  Probe saved ranges for bidirectional reachability…      │
│ Search     │  ┌────────────────────────────────────────────────────┐  │
│ ▸ Live Scan│  │ SCAN SCOPE                                         │  │
│ History    │  │ [iran_datacenter ▾] ◉ all together ○ one at a time │  │
│ Settings   │  │ ☐ whitelist only    [ Scan saved ranges ]          │  │
│            │  └────────────────────────────────────────────────────┘  │
│            │  INTERNATIONAL: 3  IRAN_ONLY: 1  UNREACHABLE: 1          │
│            │  ┌────────────────────────────────────────────────────┐  │
│            │  │ HOST            HEALTH   AVG(ms)  WHITELIST        │  │
│            │  │ 185.143.232.14  (GOOD)      18.4  (INTERNATIONAL)  │  │
│            │  │ 77.36.164.9     (MEDIUM)   128.3  (IRAN_ONLY)      │  │
│            │  └────────────────────────────────────────────────────┘  │
│ admin-c6b… │                                                          │
│ Sign out   │                                                          │
└────────────┴──────────────────────────────────────────────────────────┘
```

- **Overview** — one card per provider with a meter showing the fraction of its
  probed ranges currently reachable internationally.
- **Search** — query the bundled seed data and live discovery sources by CIDR,
  octet, provider, country, or ASN.
- **Live Scan** — run a scan over a saved category, either all ranges together
  or one CIDR at a time (per-CIDR progress and results appear as they arrive).
  Status values render as coloured pill badges; results download as a
  whitelist, CSV, or JSON.
- **History** — every past scan with a trend chart; select a row to inspect and
  export its per-host results.
- **Settings** — the same settings file the CLI and menu use, so changes apply
  everywhere.

Long result tables keep their header row pinned while scrolling, numeric
columns are right-aligned, and empty/loading/error states are rendered
explicitly rather than leaving a blank table.

---

## Quick install

**Linux / macOS / Git Bash / WSL**

```bash
git clone https://github.com/devprogrmer/gaming.git
cd gaming
./install.sh          # add --user to create a ~/.local/bin/gaming symlink
./gaming              # launch the interactive menu
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/devprogrmer/gaming.git
cd gaming
.\install.ps1
.\gaming.bat
```

---

## Full installation

### Prerequisites

- **Python 3.11 or newer.**
- `git` (to clone the repository).
- No third-party packages — the standard library is the only runtime
  dependency.

### Option 1 — install script (recommended)

```bash
./install.sh --user   # creates ~/.local/bin/gaming
```

Ensure `~/.local/bin` is on your `PATH`, then run `gaming` from anywhere.

### Option 2 — pip

```bash
python -m pip install -e .        # editable install from a clone
gaming --version
```

Installing with `pip` puts the `gaming` entry point on your `PATH` directly.

---

## Usage

### Interactive menu

Run `gaming` with no arguments (or `gaming menu`):

```
==================================================
   devprogrmer * IP Health Scanner   (v0.10.0)
==================================================
  1) Scan saved ranges (datacenter / CDN / both)
  2) Discover & save provider ranges
  3) Manage IP ranges
  4) View scan history
  5) Settings
  6) Update installed version
  7) Filter CIDRs by first octet
  8) Discover, save & scan a provider
  9) Launch web panel
  10) Look up a datacenter/provider by name
  11) What's new since your last visit
  0) Exit
----------------------------------------------------
```

| Option | What it does |
|---|---|
| **1) Scan saved ranges** | Health-scan the saved ranges (datacenter / CDN / both, Iranian or foreign). |
| **2) Discover & save provider ranges** | Discover provider ranges and save them into the matching categories. |
| **3) Manage IP ranges** | Add or remove your own CIDRs, per category. |
| **4) View scan history** | Browse previous scans stored in the local database. |
| **5) Settings** | Thresholds, ping counts, concurrency, abroad checking, provider, port scanning, alerts. |
| **6) Update installed version** | Upgrade the installation in place. |
| **7) Filter CIDRs by first octet** | Discover and filter dynamically by leading octet + datacenter. |
| **8) Discover, save & scan a provider** | One provider: discover → save → scan, in a single step. |
| **9) Launch web panel** | Start the same dashboard as `gaming web` in this process. Prompts for bind/port/TLS, and Ctrl+C stops it cleanly and returns to the menu. |
| **10) Look up a datacenter/provider by name** | Type an organization name; the registries are queried live (RDAP) and every matching org's CIDRs, ASN, and country are shown, with the option to save them for later scans. |
| **11) What's new since your last visit** | The ranges `gaming watch` discovered since this menu last looked. Reading marks them seen here only — the dashboard keeps its own notice. |
| **0) Exit** | Quit. |

### Command line

```bash
# list available discovery sources (plus seed-data freshness)
gaming sources

# discover offline (bundled sample data), Iranian datacenters, JSON output
gaming --offline discover --iran-datacenter --format json

# foreign ranges, collapse prefixes, CSV output
gaming --offline discover --foreign-datacenter --collapse --format csv -o foreign.csv

# check specific prefixes (local liveness + port probe)
gaming check 1.1.1.1 8.8.8.0/24 --ports 80,443 --format console

# look one named provider up in the registries (not just the seed list)
gaming discover --provider-name "Zenlayer" --format console

# full pipeline: discover → filter → normalize → reachability → report
gaming --offline run --country IR --ports 80,443 --format json -o report.json
```

> Global checks and non-offline discovery reach the public internet. Only use
> them against infrastructure you are authorised to probe.

### Web dashboard

```bash
# bind a random free high port (20000–65000); credentials printed once
gaming web

# localhost only, fixed port (safest on a shared server)
gaming web --bind 127.0.0.1 --port 8080

# HTTPS with a self-signed certificate (cached in the app data directory)
gaming web --tls

# regenerate the username/password and invalidate all sessions
gaming web --reset-credentials

# also re-scan the Iranian ranges every 30 minutes while the panel is up
gaming web --schedule iran --schedule-interval 1800
```

**Stopping it.** Press **Ctrl+C**. The panel stops in an orderly sequence: it
stops accepting connections, asks any in-flight scan job to finish at a safe
point (bounded wait), stops the scheduler if running, releases the listening
socket so an immediate restart on the same port works, and prints
`Web panel stopped.` Pressing Ctrl+C a second time exits immediately.

**Running it in the background.** By default the dashboard only lives as long as
the terminal or SSH session that started it:

```bash
# detach from the terminal; credentials are printed before it backgrounds.
# Output goes to web.log and the PID to web.pid in the app data directory.
gaming web --daemon --bind 127.0.0.1 --port 8787

# is it running, and since when?
gaming web --status

# stop it cleanly (SIGTERM, escalating to SIGKILL only if it refuses)
gaming web --stop
```

`--stop` routes through the exact same shutdown sequence as Ctrl+C, so a
backgrounded panel drains its jobs just as carefully as a foreground one.

For a permanent installation, use a service manager — a ready-made unit file is
in `packaging/gaming-web.service`.

### Scheduled scans and alerts

```bash
# re-scan the Iranian ranges every 15 minutes until interrupted
gaming schedule iran --interval 900

# run exactly 3 scans and exit (useful for cron/CI)
gaming schedule foreign --interval 300 --count 3
```

Each run is appended to scan history and diffed against the previous run;
verdict changes can trigger an optional webhook configured in **Settings**.

### Seed data maintenance

```bash
gaming refresh-seeds              # re-check every bundled provider (read-only)
gaming refresh-seeds --timeout 20

gaming validate-seed              # validate + stamp the last_validated marker
gaming validate-seed --no-marker  # report only, leave the marker untouched

gaming sources                    # sources + "seed data last validated: …"
```

Both commands are strictly read-only with respect to provider entries: they
flag CIDRs that no longer appear in any announced prefix, but never add, edit,
or delete a provider.

### Exhaustive discovery and continuous watch

**Exhaustive country-wide discovery** queries RIR delegated-statistics files
and resolves every allocated prefix via RIPEstat, RDAP, and WHOIS — surfacing
real but obscure hosting companies with the same full detail (CIDR, ASN,
organization, country) as well-known providers. Allocations with no public org
name are kept and labelled `(unnamed / no public org name)` rather than
dropped.

```bash
# discover every allocated range for Iran, save to local storage
gaming discover --country IR --exhaustive --save

# same, but skip IPv6 and start fresh (ignore any saved resume state)
gaming discover --country IR --exhaustive --save --no-ipv6 --no-resume

# pipe a bare IP list directly to another tool
gaming discover --country IR --exhaustive --format ip-list | sort -u > ips.txt
```

The sweep is resumable: if interrupted it picks up where it left off. Rate
limits are handled automatically (429 exponential back-off, 404 fast-skip).
Saved ranges carry the `discovered_exhaustive` origin marker so they are
distinguishable from hand-curated entries.

**Continuous watch mode** loops discovery → persist → scan → sleep
indefinitely, reusing the daemon PID-file machinery so it survives SSH
disconnect:

```bash
# run in the foreground, one iteration per hour
gaming watch --country IR --interval 1h

# detach to the background (same --stop/--status as gaming web)
gaming watch --country IR --interval 1h --daemon

# check whether the watcher is running
gaming watch --status

# stop it cleanly
gaming watch --stop

# run exactly 5 iterations and exit (useful for testing)
gaming watch --country IR --interval 30m --count 5

# what did it find while you were away? (marks them as seen)
gaming watch --whats-new
```

Interval accepts `30m`, `2h`, `1d`, or bare seconds (minimum 5 minutes).
One bad iteration logs the error and continues — the watcher never stops on
its own. The watch loop is also startable and stoppable from the web dashboard.

Each cycle records the ranges it discovered for the first time, so
`--whats-new` reports what actually appeared rather than a bare count. The
terminal and the dashboard track their own last-visited markers: checking one
leaves the other's notice intact.

**`--format ip-list`** emits one host address per line with no metadata, safe
for shell redirection. Progress, "saved N ranges", and "written: …" messages
go to stderr so stdout stays a pure IP list:

```bash
gaming discover --country IR --exhaustive --format ip-list > ips.txt
gaming discover --iran-datacenter --format ip-list -o ips.txt
```

### IP membership lookup

Check which stored CIDR an address belongs to:

```bash
# table output — shows CIDR, group, origin, country, provider
gaming check-membership 5.22.7.1

# fall back to a live RDAP lookup when nothing stored matches
gaming check-membership 203.0.113.7 --live

# machine-readable JSON
gaming check-membership 5.22.7.1 --json
```

Exit codes: **0** = at least one match found, **1** = not found, **2** =
invalid IP address. Overlapping prefixes are all reported, most specific first.
The same lookup is available in the web dashboard under **IP Lookup**
(`POST /api/lookup-ip`).

---

## How scanning works

1. **Choose a scope** — Iranian or foreign ranges. Each has a bundled, editable
   CIDR list (extend it from *Manage IP ranges*).
2. **Sample hosts** — rather than probing every address in a large range, a
   configurable number of hosts is sampled per range to keep scans fast.
3. **Discover live IPs (optional)** — a fast single-pass probe finds responsive
   hosts, which you can promote in place to a full health scan.
4. **Measure health** — latency and packet loss are measured cross-platform
   (ICMP ping, falling back to TCP where unavailable), with a live progress bar
   showing running GOOD/MEDIUM/BAD tallies.
5. **Grade the result** — each host is labelled from its latency and loss:

   | Label | Meaning |
   |---|---|
   | **GOOD** | Reachable, low latency, low packet loss. |
   | **MEDIUM** | Reachable, but higher latency or moderate loss. |
   | **BAD** | Unreachable, or high packet loss. |

6. **Check reachability from abroad** — the same hosts are probed from outside
   Iran, producing the combined verdict described in
   [Highlights](#bidirectional-reachability-iran--abroad).
7. **Store history** — every scan is written to a local SQLite database and
   stays browsable between runs.

Latency/loss thresholds, probes per host, concurrency, timeouts, and sample
size are all configurable from the **Settings** menu.

### Where data is stored

State lives in your user data directory (override with `GAMING_HOME`):

| OS | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\gaming\` |
| Linux/macOS | `$XDG_DATA_HOME/gaming/` or `~/.local/share/gaming/` |

It holds `history.db` (scan history), `settings.json` (settings and
thresholds), and `custom_ranges.txt` (CIDRs you added yourself).

---

## Exporting results

**Interactive mode:** every scan is saved to the local SQLite database
automatically and is browsable from **View scan history** — nothing extra to do.

**Command line:** use `--format` for the shape and `--output` (`-o`) to write to
a file:

```bash
# JSON to a file
gaming --offline run --country IR --format json -o report.json

# CSV to a file
gaming --offline discover --foreign-datacenter --format csv -o foreign.csv

# human-readable console output
gaming check 1.1.1.1 --ports 80,443 --format console
```

Each result row (console/JSON/CSV) contains:

| Field | Meaning |
|---|---|
| `source` | Source(s) that produced the record (e.g. `rdap+whois`). |
| `asn` | Autonomous system number as `AS<n>`. |
| `organization` | Owning organisation, when known. |
| `country` | ISO country code. |
| `provider` | Provider tag (lowercase, partially searchable). |
| `prefix` | Normalised CIDR. |
| `alive` | Local reachability (`true` / `false` / `null`). |
| `global_reachable` | Global reachability via check-host.net (`true` / `false` / `null`). |
| `open_ports` | Ports found open during probing. |
| `notes` | Provenance/diagnostic notes. |

---

## Configuration

Configuration is layered: **built-in defaults ← TOML file ← command-line
overrides**. A fully commented template lives in
[`gaming.example.toml`](gaming.example.toml):

```bash
gaming --config gaming.example.toml run --format json
```

Sections: `[general]`, `[discovery]`, `[filters]`, `[reachability]`,
`[global_check]`.

Global options (before the subcommand name):

| Option | Description |
|---|---|
| `--config, -c PATH` | Path to a TOML configuration file. |
| `--log-level LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR`. |
| `--concurrency N` | Maximum concurrent workers. |
| `--timeout SECONDS` | Per-operation timeout. |
| `--offline` | Use bundled sample data instead of live network calls. |
| `--quiet, -q` | Only log errors. |
| `--version` | Print the version. |

### Choosing the abroad-check provider

The abroad check sits behind an `AbroadProvider` interface with two
implementations: **check-host.net** (default) and **RIPE Atlas** (optional,
requires an API key). Set `abroad_provider` to `check_host`, `ripe_atlas`, or
`both` from the **Settings** menu. With no key configured, RIPE Atlas is
skipped and the tool falls back to check-host.net.

---

## Requirements

- **Python 3.11 or newer.**
- No third-party runtime dependencies (standard library only).
- ICMP ping requires the OS `ping` command; without it, the tool switches to
  TCP checks automatically.
- Global checks and live discovery require internet access.

---

## Limitations

- This tool **does not exhaustively scan entire IP ranges** — it **samples**
  each range to stay fast (sample size is configurable in *Settings*).
- ICMP ping accuracy and packet-loss figures depend on your network, firewall,
  and OS; hosts that block ICMP may be reported as **BAD** incorrectly.
- Live discovery (RDAP/WHOIS/BGP/PeeringDB) depends on public third-party
  services. On failure — or with `--offline` — the tool falls back to bundled
  sample data, which may be incomplete or out of date.
- Global checks send target addresses to a third-party service
  (check-host.net) and only work for public IPs.
- The "test path to…" proximity ping is an **approximation**: it measures from
  the nearest available RIPE Atlas probe in the source IP's network, *not* from
  the source IP itself. A remote tool cannot make an arbitrary IP originate
  traffic — that is a fact about networking, not a limitation of this tool.
- This is a **network reconnaissance and reachability testing** tool. Only use
  it on networks and hosts you own or are explicitly authorised to probe.

---

## Architecture

```
src/gaming/
├── cli.py               # argparse subcommands (menu/sources/discover/check/run/web/schedule/…)
├── pipeline.py          # orchestration: discover → process → reachability
├── config.py            # TOML loading + layered overrides (tomllib)
├── models.py            # IPRecord, Filters, normalisation helpers
├── logging_setup.py     # logging configuration
├── discovery/           # pluggable sources (shared Source interface)
│   ├── base.py          #   Source ABC + DiscoveryContext + offline fallback
│   ├── rdap.py  whois.py  asn_bgp.py  peeringdb.py  rir.py
├── processing/
│   ├── normalize.py     # dedupe, merge metadata, collapse prefixes
│   └── filters.py       # country/ASN/provider/org + Iran/foreign focus
├── reachability/
│   ├── local.py         # ping/tcp/auto liveness checks (concurrent)
│   ├── ports.py         # TCP port probing
│   └── global_check.py  # AbroadProvider interface: check-host.net + RIPE Atlas
├── reporting/
│   ├── console.py  json_export.py  csv_export.py
├── interactive/         # interactive menu-driven IP health scanner
│   ├── menu.py          #   thin menu loop (I/O + dispatch only)
│   ├── actions/         #   per-action logic, decoupled from the terminal
│   ├── scanner.py       #   ranges → live sweep → latency/abroad/port scan → grading
│   ├── pinger.py        #   cross-platform latency and loss measurement
│   ├── classify.py      #   GOOD/MEDIUM/BAD grading + bidirectional CombinedResult
│   ├── ranges.py        #   bundled, editable Iranian/foreign CIDR lists
│   ├── storage.py       #   SQLite scan history (incremental migrations)
│   ├── scheduler.py     #   recurring scheduled scans
│   ├── alerts.py        #   verdict-change detection + optional webhook
│   ├── providers.py     #   seed data + refresh/validate and last_validated marker
│   ├── filters_shared.py #  predicates shared by the menu and the web layer
│   ├── theme.py         #   shared ANSI palette + table renderer (TTY-aware)
│   ├── settings.py  progress.py  report.py  paths.py
│   └── data/            #   providers.toml and bundled range lists
├── web/                 # local dashboard
│   ├── server.py        #   http.server adapter (bind, TLS, static assets)
│   ├── lifecycle.py     #   the single shutdown path (SIGINT/SIGTERM coordinator)
│   ├── handlers.py      #   routing, auth middleware, JSON handlers
│   ├── jobs.py          #   cancellable background job manager
│   ├── auth.py  daemon.py  summary.py  assets.py
│   └── static/          #   index.html, app.css, app.js (no build step)
└── utils/http.py        # stdlib HTTP with retry/timeout
```

> For the full architectural description (the pipeline, how the CLI and menu
> paths differ, the SQLite schema, the `Config`/`Filters` vs `Settings` split,
> and the abroad-provider interface) see
> [`docs/architecture.md`](docs/architecture.md).

**Design principles:** modular and extensible (add a source by implementing
`Source` and registering it), dependency-free, fail-soft (one broken source or
host never aborts a run), and fully testable offline through dependency
injection and bundled sample data.

---

## Testing and development

```bash
python -m pytest                    # or: PYTHONPATH=src python -m pytest
python -m pip install -e ".[dev]"   # install with development tools
make check                          # lint + tests (the CI gate)
make cov                            # tests with a coverage report
make build                          # build sdist + wheel and check with twine
```

The test suite is fully offline — sources use bundled sample data and
reachability is monkeypatched, so no real network calls are made. See
[CONTRIBUTING.md](CONTRIBUTING.md) for workflow details and
[CHANGELOG.md](CHANGELOG.md) for release history.

---

## Responsible use

This tool performs network reconnaissance and reachability testing. **Only use
it on networks and hosts you own or are explicitly authorised to probe.**
Global checks send target addresses to a third-party service
(check-host.net).

It is intended for network operators, researchers, and administrators
diagnosing connectivity and reachability. Do not use it to probe infrastructure
without permission.

## License

MIT — see [LICENSE](LICENSE).
