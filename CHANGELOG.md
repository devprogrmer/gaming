# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Added
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

[Unreleased]: https://github.com/devprogrmer/gaming/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/devprogrmer/gaming/releases/tag/v0.1.0
