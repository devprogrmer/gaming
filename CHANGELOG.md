# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-07-22

### Fixed
- **`gaming web` no longer dies when the SSH session or terminal closes.**
  Previously the dashboard only stayed up as long as the terminal that launched
  it stayed open — disconnecting SSH killed the process and took the panel down.
  `gaming web` now accepts `--daemon`/`-d` to detach from the controlling
  terminal (POSIX double-fork + `setsid`), redirect its output to `web.log` in
  the app-data directory, and write a `web.pid` file; the one-time credentials
  are still printed to the console before it goes to the background. Two new
  lifecycle commands manage it without hunting for the process: `gaming web
  --status` (is it running, and since when) and `gaming web --stop` (graceful
  `SIGTERM`, escalating to `SIGKILL`). `--daemon` changes only whether the
  process survives disconnection — never the default bind or auth behavior. A
  reference `packaging/gaming-web.service` systemd unit is shipped (not
  auto-installed) for the more robust "survives reboot, auto-restarts on crash"
  setup, and both options are documented in the README. On Windows (no
  `os.fork`), `--daemon` fails loudly and points at the alternatives.

- **Iran-scoped scans no longer leak non-Iranian-located results.** Choosing an
  "Iran" origin (the interactive provider picker's Iran branch, "Scan saved
  ranges" with origin Iran, or the web dashboard's Iranian-category scan) selects
  CIDRs by their provider/ASN classification — which can attach a foreign-located
  range (an Iranian CDN's overseas PoP, an anycast edge, or a record whose
  registered country differs from where the prefix actually resolves) to an
  Iranian provider. Those Iran-origin paths now treat the record's country as the
  authoritative location signal and keep only ranges verified as `IR`. Anything
  with a missing or non-IR country is excluded from the Iran-only set and listed
  separately as "location unverified" rather than silently scanned as Iranian or
  silently dropped. (The `--country IR` CLI path already filtered strictly on the
  country field and was unaffected.)
- **"Filter CIDRs by first octet" → "All datacenters" no longer returns 0 when
  matching CIDRs exist.** With RIR-sourced records (e.g. `--country IR`
  discovery), every record carries only a country — no organization or provider
  text — so the datacenter classifier, which keys off that text, ruled every
  record out and the combined octet + "all datacenters" filter collapsed to zero
  for any octet (212, 85, 78, …). The octet match is now reported on its own
  ("N of M records match octet X") before any datacenter narrowing, and the "all
  datacenters" step splits results into three labelled buckets — classified
  datacenters, records with no provider metadata to classify (surfaced as
  "unclassified", not dropped), and records positively ruled out — so real
  matching ranges stay visible and scannable even when there's nothing to
  classify them by.
- **`./gaming` launcher could silently end up as a broken/missing command.** On a
  real server, `./gaming web` (and other subcommands) failed with
  `-bash: ./gaming: Is a directory` because a `gaming` **directory** already sat
  where the launcher file should be written (e.g. a checkout where `src/gaming/`
  was extracted alongside, or a leftover from a partial install), so the
  installer's `cat > gaming` redirect failed and left no working launcher. The
  installers (`install.sh`, `install.ps1`) now detect this up front and stop with
  an explicit message telling you exactly what is blocking the launcher and how to
  fix it, fail loudly if the launcher can't be written for any other reason
  (permissions, disk), and run a post-install self-check (`gaming --version`) so a
  broken install is caught immediately instead of when you first try `gaming web`.
  A new test covers the `gaming web` startup path (credentials + bound URL are
  printed and the server actually starts), and `CONTRIBUTING.md` documents the
  manual launcher verification steps.

## [0.5.0] - 2026-07-21

### Changed
- **Refactored `interactive/menu.py` into a thin loop + `actions/` package
  (Part E).** The `Menu` class now holds only the input loop, prompt/choice
  plumbing, and dispatch; each action's business logic moved into
  `interactive/actions/` (`scan`, `discover`, `ranges_action`, `history`,
  `settings_action`, `filter_octet`, `update_action`, plus shared `common`
  helpers). Actions take an `ActionContext` (settings, store, print/prompt/choose
  callables) instead of being bound to the terminal, so the same logic can be
  driven by a web handler. Purely structural and behaviour-preserving: all
  existing `test_interactive_menu.py` tests pass unchanged (only the two abroad
  monkeypatch targets were renamed to `check_abroad` for Part D, not for this
  refactor). Result formatting stays centralized in `report.py`.

### Added
- **`docs/architecture.md` (Part G).** A plain-Markdown architecture overview:
  the discover → process → reachability → report pipeline, how the interactive
  scanner path differs from the CLI path (the divergence Part A fixed), the
  current `history.db` SQLite schema (including which columns are nullable /
  added by migration and why), the `Config`/`Filters` vs. interactive `Settings`
  split and what each governs, and the Part D abroad-provider abstraction with a
  step-by-step guide to adding a third provider. Linked from `CONTRIBUTING.md`.
- **`gaming validate-seed` command + `[meta] last_validated` marker (Part F).**
  Validates every bundled provider's seed CIDRs against currently-announced
  prefixes (reusing the `asn_bgp` discovery source) and, unless `--no-marker` is
  passed, stamps today's date into a new `[meta].last_validated` field in
  `providers.toml`. It only reports stale-looking CIDRs and updates the marker —
  it never adds, edits, or deletes a provider entry (the marker rewrite is a
  line-oriented replace that leaves every `[[provider]]` block byte-for-byte
  intact). `gaming sources` now prints how stale the seed data is
  ("seed data last validated: …"). The marker is only stamped when at least one
  provider was actually reachable, so an offline run never claims a fresh date.
- **Pluggable abroad-check providers + service-unavailable signal (Part D).**
  The abroad (international) reachability check is now behind an
  `AbroadProvider` interface in `reachability/global_check.py`, so it no longer
  depends on a single third-party service. `CheckHostProvider` wraps the
  original check-host.net logic unchanged; a new optional `RipeAtlasProvider`
  (RIPE Atlas one-off ping, API key via `GAMING_RIPE_ATLAS_KEY`) is included
  only when a key is configured — with none set the tool falls back to
  check-host.net with no behaviour change. A new `AbroadResult` distinguishes
  three previously-indistinguishable cases: a real answer (`ok`), a non-public
  host (`not_applicable`), and a provider outage (`unavailable`) — the last now
  renders as `unavailable` (terminal + web) and persists via an additive
  `abroad_status` column, so "check-host.net is down" is visibly different from
  "this IP isn't internationally reachable". A `global_check.provider` config
  option and interactive `abroad_provider` Setting choose `check-host`,
  `ripe-atlas`, or `both`; with `both`, node-ok/node-total counts are summed
  across providers before applying `min_ok_fraction`, so one provider's outage
  doesn't decide the verdict. `global_reachability()` is retained as a
  backward-compatible tuple wrapper.
- **Recurring scheduled scans + verdict-change alerting (Part C).** A new
  stdlib `interactive/scheduler.py::ScanScheduler` (a `threading.Thread` +
  `Event`-gated sleep loop) re-runs a saved scope scan on an interval and
  appends each run to scan history, feeding the dashboard trend chart without
  manual re-runs. Exposed as `gaming schedule <scope> --interval N [--count N]`.
  Each run is fail-soft — one failed scan is logged and the schedule continues.
  A companion `interactive/alerts.py` diffs the two latest scans of a scope and,
  when a host flips between the whitelist (`INTERNATIONAL`) and a degraded state
  (`IRAN_ONLY`/`ABROAD_ONLY`/`UNREACHABLE`), logs the change and — if a webhook
  URL is configured — POSTs a JSON payload via stdlib `urllib`. Opt-in via two
  new `Settings` fields (`alert_on_change`, `alert_webhook_url`), off by default.
- **Broader provider seed data + a `refresh-seeds` re-validation pass (Part C).**
  `interactive/data/providers.toml` gained ~20 more well-known providers (Linode,
  Scaleway, Alibaba/Tencent/IBM cloud, netcup, UpCloud, StackPath, CDN77, Gcore,
  BunnyCDN, Imperva, plus more Iranian datacenters/CDNs — Sindad, MabnaTelecom,
  Pishgaman, MobinNet, Sabavision, Faraso) using the same
  `name/category/country/asns/cidrs` schema. New `providers.refresh_seed_data()`
  and a `gaming refresh-seeds` subcommand re-check every bundled CIDR against the
  provider's currently-announced prefixes (reusing the existing RIPEstat
  `asn_bgp` source) and *flag* — never delete — any that look stale. The pass is
  fully fail-soft: a provider whose lookup fails is reported as unchecked.
- **Optional common-ports scan in the interactive/web scan path (Part C).**
  `interactive/scanner.py::run_scan` now runs a plain TCP-connect probe (reusing
  `reachability/ports.py::probe_ports`) against a configurable preset
  (`80,443,22,21,25,53,3306,5432,6379,8080,8443` by default) for every host that
  answered locally, and surfaces the open ports in the terminal and web result
  tables. Gated by two new `Settings` fields — `scan_ports` (off by default) and
  `ports` — editable from the Settings menu and the web Settings form. The port
  scan is fully fail-soft and independent: a connect error never delays or aborts
  the latency/abroad passes, and dead hosts are skipped.
- **Local web dashboard (`gaming web`).** A stdlib-only
  (`http.server`/`ssl`/`secrets`/`hashlib`/`hmac`) dashboard — no new runtime
  dependency — that reuses the existing pipeline/discovery/reachability/storage
  modules with zero duplicated business logic. Pages: provider-connectivity
  home widget, partial-match Search (background discovery job), Live Scan with
  the bidirectional whitelist view + downloads, History with a dependency-free
  `<canvas>` trend chart, and Settings (shared `settings.json` with the CLI/menu).
  - **Auth:** a random username + strong password are generated on first run and
    printed once; the password is stored only as a salted `pbkdf2_hmac` hash.
    Signed-cookie sessions (`hmac` + per-install secret), an in-dashboard
    change-credentials page (confirms current password, rotates the secret to
    log out all sessions), per-IP login rate limiting, an optional bearer-token
    mode for automation, and `gaming web --reset-credentials` for recovery.
  - **Serving:** `--bind` (default `0.0.0.0`), `--port` (default auto-pick a free
    port in 20000–65000), and `--tls` (cached self-signed cert). Startup prints
    the URL, detected server IP, and a plain-HTTP-on-`0.0.0.0` security warning.
  - Static UI (HTML/CSS/JS) bundled via `importlib.resources`; no CDN, no build
    step, fully offline. Shared `matches_first_octet` / `format_bare_ips` /
    partial-CIDR search were hoisted into `interactive/filters_shared.py` so the
    terminal menu and the web layer use one implementation.

- **Bidirectional (Iran + abroad) reachability in the interactive scanner.**
  Every host scanned from the menu is now checked both locally (Iran→target)
  and, via check-host.net, from abroad, and gets a combined verdict:
  `INTERNATIONAL` (reachable both ways — the "whitelist"), `IRAN_ONLY`,
  `ABROAD_ONLY`, or `UNREACHABLE`. "not checked" (abroad check disabled,
  skipped, or non-applicable) is shown distinctly, never as a false FAIL.
  - `global_reachability` now returns `(reachable, nodes_ok, nodes_total)` and
    takes a `min_ok_fraction` threshold, so a majority of responding nodes —
    not one lucky node — decides reachability; counts surface in the UI.
  - The abroad pass runs concurrently and is fully fail-soft: a check-host.net
    timeout/exception never blocks, delays, or corrupts the local result. It is
    capped at `max_global_targets` hosts per scan (alive-first) and gated by the
    new `check_global` Settings toggle (default on for interactive scans).
  - Results tables (terminal + history) gain `ABROAD` (`OK (n/total)` /
    `FAIL (n/total)` / `not checked`) and `WHITELIST` (combined verdict) columns,
    colour-coded; scans print an `International / Iran-only / Abroad-only /
    Unreachable` summary line. The bare-IP export can be limited to whitelisted
    (`INTERNATIONAL`) hosts via the `export_international_only` toggle.
  - Scan history persists the new fields via an additive, idempotent SQLite
    migration (`ALTER TABLE results ADD COLUMN ...`), so existing `history.db`
    files load unchanged and pre-migration rows read back as "not checked".

### Fixed
- **Live discovery no longer silently falls back to sample data.** The
  interactive "Discover & save provider ranges" flow now seeds the pipeline
  with the bundled providers' ASNs/countries; without seeds every source
  early-returned nothing and the pipeline swapped in the 12-record sample set.
  A seeded run now returns thousands of real, current prefixes across all
  sources. Two contributing causes were fixed: the bulk discovery pass uses a
  longer 15s per-request timeout (RIPEstat/WHOIS routinely exceed the 5s ad-hoc
  default), and the WHOIS source caps how much of a `-i origin` dump it reads
  (a single large transit AS returned ~16 MB / 20+ s and always timed out).
- **Per-request error visibility.** `DiscoveryContext.verbose_errors` (set by
  the interactive discovery pass) surfaces each failed per-ASN/per-source
  request at WARNING with its real exception *type* and message instead of a
  terse DEBUG "failed", so a genuine DNS/refused/timeout/TLS/HTTP/parse cause is
  no longer masked as a generic sample-data fallback. `pipeline.discover` gained
  `timeout` and `verbose_errors` overrides.

### Added
- **Targeted "Discover, save & scan a provider" flow** (menu option 8): pick an
  origin (Iran / Foreign), then a specific known provider from a numbered list
  built from `providers.toml` (Pars Pardazesh, Asiatech, ArvanCloud, Hetzner,
  OVH, DigitalOcean, Cloudflare, Fastly, …) or "All". The chosen provider is
  discovered live (seeded by its ASNs), its newly discovered CIDRs are persisted
  into the correct Manage IP Ranges category automatically, and its hosts are
  scanned immediately — discover → save → scan as one continuous flow with no
  intermediate prompts. The existing "Scan saved ranges" and "Manage IP ranges"
  menus are unchanged.
- `providers.load_providers()` / `providers_for_origin()` and a `Provider`
  dataclass backing the provider picker.

## [0.4.0] - 2026-07-17

### Added
- **Persistent, category-separated range storage.** Discovered CIDRs are now
  auto-saved into Manage IP Ranges under four categories — `iran_datacenter`,
  `iran_cdn`, `foreign_datacenter`, `foreign_cdn` — and survive restarts. The
  custom-ranges file gained an `origin` (`custom`/`discovered`) and
  `country`/`provider` metadata, with legacy two-field files still parsing.
- **Discover & save flow** (`persist_records` + bundled `data/providers.toml`
  seed data) aggregates CIDRs across many datacenter/hosting/cloud/CDN providers
  (Cloudflare, Fastly, Akamai, Google, AWS, Azure, Meta, OVH, Hetzner,
  DigitalOcean, Vultr, Oracle + major Iranian providers) for both origins — not
  a single provider.
- **Class-aware scan prompt**: choose origin (Iran / Foreign / Both) and class
  (Datacenter / CDN-Cloud / Both), then scan the matching saved CIDRs. Classes
  never leak into each other (`classify_category`, `is_datacenter_only`).
- **Iran-origin latency reporting**: `scanner.summarize_by_group` buckets live
  probes by destination country/provider and reports which answers fastest from
  the (Iranian) server. Latency is measured, not geolocated.
- **`devprogrmer` banner** and a restructured, ANSI-safe menu; clean
  copy-paste-ready bare-IP output with per-IP category/provider/country lines.
- Manage IP ranges can list by category with `[discovered]`/`[custom]` tags and
  add/remove per category.

## [0.3.0] - 2026-07-17

### Added
- **Separated scan categories** in the interactive menu, each with its own
  discovery → filtering → scan flow so results never mix:
  - *Scan Datacenters* — ordinary datacenter/hosting ranges only; major
    CDN/cloud/edge/WAF providers (Cloudflare, Fastly, Akamai, Meta, Google
    edge, ArvanCloud, …) are excluded in the actual filtering step.
  - *Scan Foreign CDN/Cloud Providers* — targets exactly those global CDN/cloud
    platforms.
  - *Scan Iranian CDN Providers* — Iran-scoped CDN/edge networks via best-effort
    org/provider + country heuristics.
  - New predicates `is_datacenter_only`, `is_foreign_cdn`, `is_iranian_cdn` in
    `processing.filters` enforce the separation.
  - **Region selection** (Middle East / Europe / Asia / All) applied after the
    scan type is chosen, narrowing which CIDRs reach the scanner.
  - **Clean bare-IP output**: a copy-paste-ready block of alive IPs, one per
    line with no prefixes/symbols/colours, printed for every scan category.
  - **Update installed version** menu option reusing the `gaming update` flow.
- **In-place update mechanism** to upgrade a deployed installation to a new
  release without deleting the previous one first:
  - `gaming update` subcommand (`--source PATH`, `--no-pull`) that reuses the
    existing virtualenv and runs `pip install --upgrade` over the current
    install; optionally `git pull --ff-only`s the source first.
  - `update.sh` / `update.ps1` wrappers mirroring the installers.
  - User state (scan history, settings, custom ranges) lives outside the
    install tree and is preserved across upgrades.

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

[Unreleased]: https://github.com/devprogrmer/gaming/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/devprogrmer/gaming/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/devprogrmer/gaming/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/devprogrmer/gaming/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/devprogrmer/gaming/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devprogrmer/gaming/releases/tag/v0.1.0
