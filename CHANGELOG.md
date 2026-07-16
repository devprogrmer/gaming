# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-16

### Added
- **Interactive, menu-driven IP health scanner** (`gaming menu`, also the
  default when `gaming` is run with no subcommand):
  - Iranian and foreign IP-range workflows with bundled, editable CIDR lists.
  - Alive-IP discovery (quick single-probe sweep) with optional promotion to a
    full health scan.
  - Cross-platform latency + packet-loss measurement (no `fping`/`tail`/`watch`
    required) with a live, dependency-free progress bar.
  - Simplified **GOOD / MEDIUM / BAD** health classification (Check-Host style)
    with user-tunable thresholds.
  - Persistent scan history in a local SQLite database, browsable across runs.
  - `Manage IP ranges` and `Settings` menus for adding custom ranges and
    adjusting classification/scan parameters.
- One-command installers: `install.sh` (Linux/macOS/Git Bash/WSL) and
  `install.ps1` (Windows) that bootstrap a virtualenv, install the tool, and
  create a `gaming` launcher.
- New `gaming.interactive` subpackage and an offline test suite covering
  classification, ranges, storage, scanner, and the menu loop.

### Changed
- The `rdap`, `whois`, and `peeringdb` discovery sources now perform real
  live lookups instead of falling straight through to sample data:
  - `rdap` resolves each seed ASN's autnum (organization + country via the
    RDAP bootstrap redirector) and enriches its announced prefixes.
  - `whois` issues an inverse `-i origin ASxxxx` query over port 43 and parses
    the returned RPSL `route:`/`route6:` objects.
  - `peeringdb` resolves the network organization (`/api/net`) and emits one
    record per exchange peering IP (`/api/netixlan`).
  All three retain graceful offline/failure fallback to bundled sample data.
- `gaming` no longer requires a subcommand; running it bare opens the menu.

### Added (sources)
- Offline, mocked tests for the three live-lookup sources (15 tests).

## [0.1.0] - 2026-07-16

### Added
- Initial release of the `gaming` network discovery and reachability CLI.
- Pluggable discovery sources: `rdap`, `whois`, `asn_bgp` (RIPEstat/BGP),
  `peeringdb`, `rir`, each with graceful offline sample-data fallback.
- Filtering by country, ASN, provider, and organization, plus Iranian- and
  foreign-datacenter focus modes.
- Prefix normalization: validation, de-duplication with metadata merge, and
  optional CIDR collapsing.
- Reachability: concurrent local alive checks (`ping`/`tcp`/`auto`), TCP port
  probing, and opt-in global reachability via check-host.net (public IPs only).
- Output to console, JSON, and CSV.
- Layered configuration (defaults → TOML → CLI overrides), logging,
  thread-pool concurrency, and fail-soft error handling.
- CLI subcommands: `sources`, `discover`, `check`, `run`.
- Test suite (52 tests, fully offline) and packaging for distribution.

[Unreleased]: https://github.com/devprogrmer/gaming/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/devprogrmer/gaming/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devprogrmer/gaming/releases/tag/v0.1.0
