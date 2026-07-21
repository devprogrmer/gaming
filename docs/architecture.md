# Architecture

A map of how `gaming` fits together, for contributors and future-me. It reflects
the code as it exists after Parts A (bidirectional reachability), D (abroad-check
provider abstraction), E (menu refactor), and F (seed validation). Keep it in
sync when those areas change.

`gaming` is a stdlib-only Python 3.11+ tool for **IP-range discovery** and
**reachability scanning**, designed to run from a server in Iran and answer
"which networks are reachable, from Iran and from abroad?"

---

## 1. The pipeline: discover → process → reachability → report

The core data flow is a four-stage pipeline. Each stage is a separate package so
a failure in one source or host never aborts the run (fail-soft throughout).

```
                 ┌───────────┐   ┌────────────┐   ┌──────────────┐   ┌──────────┐
  seeds/config → │ discover  │ → │  process   │ → │ reachability │ → │  report  │
                 │discovery/*│   │processing/*│   │reachability/*│   │reporting/*│
                 └───────────┘   └────────────┘   └──────────────┘   └──────────┘
                   RDAP/WHOIS/     normalize +       local alive +      console /
                   BGP/PeeringDB   filter + collapse  ports + abroad     JSON / CSV
```

- **discover** (`discovery/`) — pluggable `Source` subclasses (`rdap`, `whois`,
  `asn_bgp`, `peeringdb`, `rir`), each seeded by ASNs/countries and each with an
  offline sample-data fallback. `Source.discover()` wraps the live lookup with
  error handling so one dead source degrades to samples instead of raising.
- **process** (`processing/`) — `normalize.py` validates, de-dupes, merges
  metadata, and optionally collapses prefixes; `filters.py` applies
  country/ASN/provider/org filters and the Iran/foreign datacenter/CDN
  classification (`classify_category`).
- **reachability** (`reachability/`) — `local.py` (ping/tcp/auto alive checks),
  `ports.py` (TCP-connect port probing), and `global_check.py` (abroad
  reachability; see §4).
- **report** (`reporting/`) — `console.py`, `json_export.py`, `csv_export.py`.

`pipeline.py` orchestrates the CLI path: `run_pipeline()` → `discover()` →
`process()` → `check_reachability()` (which runs local alive checks, optional
port probes, and—if `global_check.enabled`—the abroad pass via `_run_global_checks`).

### CLI path vs. interactive-scanner path

There are **two** reachability entry points, and they historically diverged —
the gap that Part A closed:

- **CLI path** (`gaming discover/check/run`) — `pipeline.py` operates on
  `models.IPRecord`, setting `IPRecord.alive`, `IPRecord.global_reachable`, and
  `IPRecord.open_ports`. This path always supported local + abroad + ports.
- **Interactive path** (`gaming menu`) — `interactive/scanner.py` operates on the
  lighter `interactive/classify.py::ProbeResult` (latency/loss only) produced by
  `interactive/pinger.py`. Originally this path called **only** the local probe;
  it never invoked `global_reachability` or `probe_ports`, so the menu — the
  interface most users actually use — had no abroad or port visibility.

Part A/C wired the existing abroad and port checks into the interactive scanner
by wrapping each `ProbeResult` in a **`CombinedResult`** (`classify.py`) that adds
`abroad_reachable`, `abroad_nodes_ok/total`, `abroad_status` (§4), and
`open_ports`. `scanner.run_scan()` now runs three passes:

1. local latency pass (`pinger.scan_hosts`),
2. an independent, capped, alive-first **abroad pass** (`_run_abroad_pass` →
   `check_abroad`), and
3. an optional **port pass** (`_run_port_scan` → `probe_ports`), gated by
   Settings.

The two verdicts combine via `classify_bidirectional(iran, abroad)` into
`INTERNATIONAL` / `IRAN_ONLY` / `ABROAD_ONLY` / `UNREACHABLE`. Every pass is
fail-soft and independent: an abroad-service outage or a port error never blocks
or corrupts the local result.

### Interactive menu structure (Part E)

`interactive/menu.py` is a **thin** I/O loop: it renders the numbered menu, reads
input, and dispatches. Each action's logic lives in `interactive/actions/`
(`scan`, `discover`, `ranges_action`, `history`, `settings_action`,
`filter_octet`, `update_action`, plus shared `common` helpers). Actions take an
`ActionContext` (settings, store, and `print_`/`prompt`/`choose` callables)
instead of the terminal, so the same functions can back a web handler. Result
formatting stays centralized in `interactive/report.py` — actions never format
tables themselves.

The **web dashboard** (`web/`, Part B) reuses the pipeline, scanner, storage, and
the shared helpers in `interactive/filters_shared.py`, with no duplicated
business logic.

---

## 2. SQLite schema (`history.db`)

Interactive scans persist to a SQLite database under the app home (§3), via
`interactive/storage.py::HistoryStore`. Two tables:

### `scans` — one row per scan run

| column       | type    | notes                              |
|--------------|---------|------------------------------------|
| `id`         | INTEGER | PK, autoincrement                  |
| `started_at` | TEXT    | ISO-8601 UTC timestamp             |
| `scope`      | TEXT    | e.g. `iran`, `foreign_cdn`         |
| `total`      | INTEGER | host count                         |
| `good`       | INTEGER | GOOD verdict count                 |
| `medium`     | INTEGER | MEDIUM verdict count               |
| `bad`        | INTEGER | BAD verdict count                  |

### `results` — one row per probed host

Base columns (original schema):

| column     | type    | notes                                    |
|------------|---------|------------------------------------------|
| `id`       | INTEGER | PK, autoincrement                        |
| `scan_id`  | INTEGER | FK → `scans(id)` ON DELETE CASCADE       |
| `host`     | TEXT    | probed IP                                |
| `verdict`  | TEXT    | GOOD / MEDIUM / BAD (local health)       |
| `avg_ms`   | REAL    | mean RTT, nullable                       |
| `loss_pct` | REAL    | packet loss %, nullable                  |
| `sent`     | INTEGER | probes sent                             |
| `received` | INTEGER | probes received                         |

Additive columns, applied by an **idempotent migration** in
`HistoryStore.initialize()` (`PRAGMA table_info(results)` → `ALTER TABLE ... ADD
COLUMN` for any missing one). This keeps pre-existing on-disk databases working:
rows written before a column existed read back `NULL`, surfaced as "not checked".

| column               | type    | added for | notes                                             |
|----------------------|---------|-----------|---------------------------------------------------|
| `abroad_reachable`   | INTEGER | Part A    | 1/0/NULL — abroad verdict (NULL = not checked)     |
| `abroad_nodes_ok`    | INTEGER | Part A    | responding abroad nodes that succeeded             |
| `abroad_nodes_total` | INTEGER | Part A    | responding abroad nodes total                      |
| `combined_verdict`   | TEXT    | Part A    | INTERNATIONAL / IRAN_ONLY / ABROAD_ONLY / UNREACHABLE |
| `open_ports`         | TEXT    | Part C    | comma-separated open ports, or NULL                |
| `abroad_status`      | TEXT    | Part D    | `ok` / `unavailable` / `not_applicable` / NULL     |

`abroad_status` is what lets the UI distinguish a **provider outage**
(`unavailable`) from a genuine **not-reachable** (`abroad_reachable = 0`,
status `ok`) from **never checked** (NULL / `not_applicable`). Pre-Part-D rows
have `abroad_status = NULL` and render exactly as before.

An index `idx_results_scan` covers `results(scan_id)`.

---

## 3. Configuration: two surfaces, on purpose

There are **two** independent configuration surfaces. They govern different
paths and are intentionally not merged:

### `config.py` — `Config` + `Filters` (the CLI pipeline)

- Layered: built-in defaults → optional TOML file → CLI-flag overrides.
- Sections: `[general]`, `[discovery]`, `[filters]`, `[reachability]`,
  `[global_check]`.
- `Config.to_filters()` produces a `models.Filters` (country/ASN/provider/org +
  Iran/foreign focus) that drives `discover()`/`process()`.
- Governs the `gaming discover/check/run` path. `global_check.provider`
  (`check-host` / `ripe-atlas` / `both`) selects abroad providers here.

### `interactive/settings.py` — `Settings` (the menu + web dashboard)

- A standalone JSON file (`settings.json`) — **not** part of `Config` — so the
  interactive tunables stay independent of the CLI config schema.
- Holds classification thresholds (GOOD/MEDIUM latency & loss), scan behaviour
  (ping count, concurrency, timeout, sampling, caps), abroad-check options
  (`check_global`, `max_global_targets`, `global_min_ok_fraction`,
  `abroad_provider`), the optional port scan (`scan_ports`, `ports`), export
  (`export_international_only`), and scheduled-scan alerting (`alert_on_change`,
  `alert_webhook_url`).
- Shared by the terminal menu and the web Settings page (one file, one source of
  truth). `Settings.clamped()` coerces every field into a sane range on load and
  save.

**Rule of thumb:** the CLI path reads `Config`/`Filters`; the menu and web
dashboard read `Settings`. `abroad_provider` exists in both (one per surface).

### App-home files (`interactive/paths.py`)

State lives outside the install tree (so upgrades preserve it), under
`$GAMING_HOME` (if set), else `%LOCALAPPDATA%\gaming` (Windows) or
`$XDG_DATA_HOME/gaming` / `~/.local/share/gaming` (Unix):

| file                 | purpose                                   |
|----------------------|-------------------------------------------|
| `history.db`         | scan history (§2)                         |
| `settings.json`      | interactive/web `Settings`                |
| `custom_ranges.txt`  | user-added CIDRs (categorized)            |
| `web_credentials.json` | hashed dashboard credentials (Part B)   |
| `web_cert.pem` / `web_key.pem` | cached self-signed TLS (Part B)  |

---

## 4. Abroad-check provider abstraction (Part D)

The abroad ("is this reachable from outside Iran?") check lives behind a small
interface in `reachability/global_check.py`, so it no longer depends on a single
third-party service:

```
AbroadProvider (ABC)
  .check(host, *, timeout, port, min_ok_fraction) -> AbroadResult
        ├── CheckHostProvider   (check-host.net; the original logic)
        └── RipeAtlasProvider   (RIPE Atlas one-off ping; optional, needs a key)
```

- **`AbroadResult`** carries `reachable` (True/False/None), `nodes_ok`,
  `nodes_total`, and a `status` of `ok` / `not_applicable` / `unavailable`.
  This is what makes a **service outage** distinguishable from **not reachable**
  and from **not checked** — previously all three collapsed to `False`/`None`.
- **`RipeAtlasProvider`** reads its API key from the `GAMING_RIPE_ATLAS_KEY`
  environment variable (never hardcoded). With no key it is silently skipped, so
  an unconfigured install falls back to check-host.net with no behaviour change.
- **`build_providers(choice)`** turns `"check-host"` / `"ripe-atlas"` / `"both"`
  into a provider list (dropping RIPE Atlas when unkeyed).
- **`check_abroad(host, providers=…)`** runs each provider fail-soft and merges
  the results with `combine_results()`, which **sums node-ok/node-total counts
  across providers before** applying `min_ok_fraction` — so one provider's outage
  or rate-limit can't by itself decide the verdict.
- **`global_reachability()`** is retained as a thin backward-compatible wrapper
  returning the original `(reachable, nodes_ok, nodes_total)` tuple.

### Adding a third abroad provider

1. Subclass `AbroadProvider` in `global_check.py` (or a new module) and implement
   `check(...) -> AbroadResult`. Return `AbroadResult.not_applicable()` for
   non-public hosts, `AbroadResult.unavailable()` on any network/parse failure
   (never raise), and `AbroadResult.ok(reachable, ok, total)` for a real answer.
2. Give it a unique `name` and add it to `build_providers()` (and the
   `PROVIDER_*` choices) behind whatever config/key gating it needs.
3. Add its choice to `Config` `global_check.provider` and/or the interactive
   `Settings.abroad_provider` field (both accept the same choice strings).
4. Mock its HTTP layer in `tests/test_reachability.py` following the existing
   check-host/RIPE-Atlas stubs — no real network calls.

Because callers depend only on `AbroadProvider` / `AbroadResult`, no scanner,
storage, or reporting code needs to change to add a provider.

---

## 5. Seed data + freshness (Part F)

`interactive/data/providers.toml` is a hand-maintained snapshot mapping known
providers (name, category, country, ASNs, CIDRs) used to seed discovery. A
`[meta].last_validated` marker records when it was last checked.

`gaming validate-seed` (and the read-only `gaming refresh-seeds`) re-checks each
provider's seed CIDRs against currently-announced prefixes via the `asn_bgp`
source, **reports** stale-looking entries (never deletes them), and — for
`validate-seed`, unless `--no-marker` — stamps today's date into
`[meta].last_validated`. The marker is surfaced by `gaming sources`. The rewrite
is line-oriented and only touches the marker; `[[provider]]` blocks are left
intact.

---

## Design principles

- **Stdlib only.** No runtime dependencies; runs anywhere Python 3.11+ does.
- **Fail-soft.** A dead source, host, or third-party service degrades gracefully
  and never aborts a run.
- **Backward compatible on disk.** Schema changes are additive migrations; older
  `history.db` / `settings.json` / `custom_ranges.txt` / `providers.toml` keep
  working.
- **Offline-testable.** Sources fall back to sample data; network layers are
  mocked in tests — the suite makes no real network calls.
