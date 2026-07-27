# Exhaustive discovery, continuous watch mode, IP-only output, reverse IP lookup

Date: 2026-07-27
Status: approved design, ready for implementation planning
Baseline test suite: 332 passed, 1 skipped

## Problem

Discovery today is anchored to a curated seed list — `interactive/data/providers.toml`,
about 24 named providers — plus live RDAP/WHOIS/ASN-BGP/PeeringDB/RIR lookups keyed by
those seeds' ASNs and countries. Four gaps follow from that design:

1. A country sweep can only surface providers someone thought to hand-add. A real,
   registered, legitimate Iranian hosting company that is merely obscure never appears.
2. Discovery and scanning are one-shot. There is no way to leave the tool running on a
   server and have it keep working unattended.
3. Every output format carries metadata. There is no way to get a bare address list for
   piping into another tool.
4. There is no way to ask "which of my saved ranges contains this IP?".

## Goals

* Given a country code, find **every** currently-allocated IP range and ASN associated
  with it — with full metadata (CIDR, ASN, organization, country) for obscure and famous
  organizations alike, and **zero difference in treatment between them**.
* Run discovery + scanning continuously and unattended, surviving SSH disconnection.
* Emit a bare-IP-per-line format suitable for shell pipelines.
* Look up a single IP against all stored ranges, from both the web panel and the CLI.

## Non-goals

* IPv6 exhaustive enumeration. The RIR delegated parser already handles only `ipv4`
  records; that limit is retained. IPv6 prefixes discovered via announced-prefixes are
  still recorded, they are simply not enumerated from delegated stats.
* Replacing the seeded discovery path. Exhaustive mode is additive and opt-in.
* Any new runtime dependency. Everything below is Python 3.11+ standard library.

## Constraints

* Stdlib only, no new runtime dependencies.
* Fail-soft throughout: one bad source, pass, or cycle logs and continues.
* Backward compatible with existing on-disk data, settings, and config.

---

## Architecture

Five new modules plus targeted edits to existing ones.

| New module | Purpose |
| --- | --- |
| `discovery/exhaustive.py` | Part 1 sweep engine (ASN-first, RIR cross-check) |
| `discovery/checkpoint.py` | Part 1 resume state |
| `interactive/watch.py` | Part 2 continuous discovery+scan loop |
| `reporting/ip_list.py` | Part 3 bare-IP exporter |
| `interactive/membership.py` | Part 4 IP-in-CIDR lookup |

| Edited module | Change |
| --- | --- |
| `utils/http.py` | HTTP status awareness: 429 backoff, 404 fast-skip |
| `processing/filters.py` | Country-decides classification fallback |
| `interactive/ranges.py` | `discovered_exhaustive` origin |
| `interactive/paths.py` | Checkpoint, watch PID, watch log paths |
| `interactive/settings.py` | `watch_country`, `watch_interval_seconds` |
| `interactive/menu.py` | "Full country scan" menu entry |
| `cli.py` | `--exhaustive`, `watch`, `check-membership`, `--format ip-list` |
| `web/handlers.py` | Exhaustive, monitoring, lookup, ip-list export routes |
| `web/static/*` | Monitoring panel, IP lookup box, ip-list download button |
| `reporting/__init__.py` | Register `ip-list` in `export()` |

---

## Part 1 — Exhaustive country-wide discovery

### Why not a `Source` subclass

`discovery/base.py::Source.discover()` substitutes `_sample_data()` whenever
`_discover_online()` returns empty or raises. That is correct for the seeded pipeline —
it guarantees output — but it is actively wrong for an exhaustive sweep, where a network
failure would silently inject fabricated records such as `"Pars Pardazesh (sample)"` into
a result set the user believes is a real registry snapshot.

`ExhaustiveDiscovery` is therefore a plain class that calls `utils/http.py` helpers
directly. It never falls back to sample data. An empty result means "found nothing",
never "made something up".

It reuses the existing logic that is safe to reuse:

* `RDAPSource._parse_autnum` — already a `@staticmethod`, extracts `(organization,
  country)` from an RDAP autnum object including the jCard/`vcardArray` `fn` field.
* `RIRSource._parse_delegated` — the delegated-stats parser.
* `models.normalize_asn` / `normalize_prefix`.

### Traversal

ASN-first with an RIR cross-check. Roughly 600 requests plus a tail, versus 4000+ for a
naive prefix-first walk.

```
1. RIPEstat country-asns(IR)      -> ~300 ASNs
2. per ASN (bounded thread pool):
     announced-prefixes           -> prefixes
     RDAP autnum                  -> organization, country
3. RIR delegated-stats(IR)        -> ~4000 allocations
4. RIR prefix not covered by (2):
     RDAP ip lookup               -> organization
     else                         -> "(unnamed / no public org name)"
```

Step 2 uses a `ThreadPoolExecutor` whose width comes from the existing discovery
concurrency setting. An organization cache keyed by ASN means each ASN's RDAP autnum is
fetched at most once per run, and survives resume via the checkpoint.

Step 4 exists so that allocated-but-unannounced space still surfaces. It is the tail
that makes the sweep exhaustive rather than merely BGP-visible.

### Completeness guarantee

Every emitted `IPRecord` carries `prefix`, `asn`, `organization`, and `country`. There is
no branch anywhere in the engine that consults `providers.toml`, checks whether an
organization is "known", or varies the fields populated based on how recognizable a name
is. An obscure Iranian host resolved from RDAP produces a record structurally identical
to one for Cloudflare.

The `"(unnamed / no public org name)"` label is reserved for the genuine exception: an
allocation present in RIR delegated stats with no matching RDAP or WHOIS organization
anywhere in public registry data. Such records are **included and labeled**, never
dropped and never left blank.

### HTTP status awareness

`utils/http.py` currently treats every `URLError`/`OSError` identically with linear
backoff and no status-code inspection. Two additions:

* **429** — raise/handle as rate-limiting. Honour a `Retry-After` header when present;
  otherwise exponential backoff (0.5s, 1s, 2s, 4s) capped at a ceiling. This is what
  keeps a 300-ASN sweep from being throttled into failure.
* **404** — raise a distinct `NotFoundError` (subclass of `HTTPError`, so existing
  `except HTTPError` callers are unaffected) **immediately, with no retry**. An ASN with
  no RDAP record is a normal outcome, not a transient failure; retrying it three times
  wastes seconds per ASN across hundreds of ASNs.

Existing callers keep working because `NotFoundError` is an `HTTPError` and the retry
behaviour for every other status is unchanged.

### The silent-drop defect

`processing/filters.py::classify_category` returns `None` for any record whose
organization text matches no keyword, and `interactive/ranges.py::persist_records` then
discards it. An obscure but real Iranian host — exactly what Part 1 exists to surface —
matches no keyword and is silently lost.

Resolution, per the approved "country decides, keywords only refine" rule:

```python
def classify_category(rec, *, fallback_by_country: bool = False) -> str | None:
    ...existing keyword logic...
    if fallback_by_country:
        return "iran_datacenter" if _is_iran(rec) else "foreign_datacenter"
    return None
```

`persist_records` gains the same keyword-only flag and passes it through. Keyword
matching still decides CDN vs datacenter — it is a refinement, never a gate. The default
stays `False`, so the seeded path and all existing tests are unchanged.

### Resume

`discovery/checkpoint.py` persists JSON at `paths.exhaustive_checkpoint_path(country)`:

```json
{
  "country": "IR",
  "version": 1,
  "started_at": "2026-07-27T10:00:00+00:00",
  "asns_done": ["AS12880", "AS58224"],
  "org_cache": {"AS12880": ["Information Technology Company", "IR"]},
  "records": [ ... ]
}
```

Written atomically (temp file + `os.replace`) every 25 completed ASNs and once when the
ASN phase finishes. On startup, a checkpoint for the same country causes completed ASNs
to be skipped and their records restored. Successful completion deletes the file.
`--restart` ignores and overwrites any existing checkpoint.

A checkpoint whose `version` is unrecognized, or whose JSON is corrupt, is discarded with
a warning and the run starts clean — a bad checkpoint must never prevent a run.

### Persistence

`interactive/ranges.py::_ORIGINS` gains `"discovered_exhaustive"`. This matters because
`_read_custom` coerces any unrecognized origin to `"custom"`; without the addition the
marker would silently vanish on the next read. `save_discovered` gains an `origin`
parameter defaulting to `"discovered"`, so existing callers are unaffected.

Files written by earlier versions contain only `custom`/`discovered` and read back
identically.

### Surfaces

* CLI: `gaming discover --country IR --exhaustive [--restart]`
* Menu: new option 10, "Full country scan"
* Web: `POST /api/exhaustive` starting a `JobManager` job that polls `job.cancelled()`
  between ASNs, so shutdown stops it at a safe point rather than mid-write

### Tests

* Exhaustive pull against mocked RIPEstat/RIR/RDAP responses for a country with a mix of
  named and unnamed ASNs; assert every record has prefix + ASN + organization + country,
  and that the unnamed one is labeled rather than dropped.
* Resumability: interrupt a mocked run partway, restart, assert completed ASNs are not
  re-fetched and prior records are retained.
* Rate limiting: a mocked 429 triggers backoff and a retry; a mocked 404 is skipped
  immediately with no retry.
* Sample-data isolation: a run where every network call fails yields zero records, and
  specifically contains no record whose organization ends in `(sample)`.

---

## Part 2 — Continuous 24/7 watch mode

### Extending, not duplicating

`interactive/scheduler.py::ScanScheduler` already is a fail-soft threaded interval loop:
daemon thread, `threading.Event`-gated sleep (`while not self._stop.wait(interval)`),
`run_once()` that records errors rather than raising, and a `_MIN_INTERVAL_SECONDS`
floor. Part 2 reuses that structure rather than writing a second loop.

`interactive/watch.py::WatchLoop` keeps the same lifecycle surface — `start()`, `stop()`,
`running`, `run_once()` — with a different cycle body:

1. Run Part 1 exhaustive discovery for the configured country.
2. Persist results via `persist_records(..., fallback_by_country=True)` with origin
   `discovered_exhaustive`.
3. Expand the newly-discovered set to hosts via `ranges.expand_hosts`.
4. Run a bidirectional (Iran + abroad) reachability scan over those hosts.
5. `HistoryStore.save_scan(...)` so results accumulate across runs rather than being
   discarded between them.
6. Sleep for the configured interval; repeat indefinitely.

### Fail-soft granularity

Failure isolation is **per phase, not per cycle**. A discovery failure still lets the
scan phase run over previously-saved ranges; a scan failure still leaves the discovery
results persisted. Each phase catches broadly, logs, records the error on the cycle
state, and continues. Nothing propagates out of `run_once()`, so no single bad pass can
terminate the loop.

### Daemonization

`web/daemon.py` needs no changes: `read_pid`, `write_pid`, `remove_pid`, `status`,
`stop`, and `daemonize` all already accept an optional `pid_path`/`log_path`. `gaming
watch` passes `paths.watch_pid_path()` and `paths.watch_log_path()` and inherits the
POSIX double-fork detach, the SIGTERM-with-SIGKILL-escalation stop, the stale-PID-file
cleanup, and the Windows `OpenProcess` liveness check.

On Windows `daemonize` raises `DaemonError` as it already does for `gaming web`; the
message will point at foreground use and the systemd unit, consistent with existing
behaviour.

* `gaming watch --country IR --interval 1h [--daemon]`
* `gaming watch --stop`
* `gaming watch --status`

`--interval` accepts a plain number of seconds or a suffixed duration (`30s`, `15m`,
`1h`, `2d`), parsed by a small helper and clamped to `_MIN_INTERVAL_SECONDS`.

### Shared configuration

`Settings` gains:

```python
watch_country: str = ""            # "" means "not configured"
watch_interval_seconds: int = 3600 # clamped to >= _MIN_INTERVAL_SECONDS
```

Both are read by `clamped()`. Because settings are shared, the web dashboard's
Monitoring panel starts **the same `WatchLoop`** rather than a parallel implementation —
it writes the settings and starts the loop through the identical entry point the CLI
uses.

**Where the web-started loop lives.** The Monitoring panel starts a `WatchLoop` *in the
web server process*, held on the `WebApp` as a single optional instance, with
`POST /api/monitoring` (start/stop) and `GET /api/monitoring` (status). This keeps one
loop implementation and one lifecycle to reason about. The consequence is explicit and
must be documented in both READMEs: a web-started loop stops when the web server stops.
For a monitor that outlives the dashboard, run `gaming watch --daemon`, which is
independent and has its own PID file. The two are separate processes; `GET
/api/monitoring` reports the standalone daemon's status too (via
`daemon.status(paths.watch_pid_path())`) so the panel never claims nothing is running
when a daemon is.

`WatchLoop.stop()` is called from the web server's existing shutdown coordinator, so a
web-started loop shuts down cleanly with the rest of the process rather than being killed
mid-write.

Older `settings.json` files lack both keys; `load_settings` already tolerates missing
fields by falling back to dataclass defaults, so existing installs pick up
`watch_country=""` (disabled) with no migration.

### Tests

All tests inject a fake clock and stub the stop-event's `wait`, so multi-iteration
behaviour is asserted in milliseconds. No test requires a real multi-hour run.

* The loop iterates multiple times with a mocked short interval and a small mocked
  discovery+scan.
* History accumulates: N iterations produce N saved scans, and earlier results survive.
* A single failing iteration — discovery raising, then scan raising — does not stop
  subsequent iterations, and both failures are logged.
* `--stop` against a running watch PID file terminates it and removes the file.

---

## Part 3 — Bare IP-address output

### Reuse, not reimplementation

Both halves already exist:

* `interactive/ranges.py::expand_hosts(cidrs, *, sample_per_range, max_hosts)` — bounded,
  evenly-spaced host expansion by index arithmetic, so large prefixes stay cheap.
* `interactive/filters_shared.py::format_bare_ips(hosts)` — de-duplicates preserving
  order, strips whitespace, emits one bare address per line with no prefixes, symbols,
  colours, or headers.

The web dashboard's existing `/api/export?kind=whitelist` already uses `format_bare_ips`.
Part 3 does not introduce a second implementation; `reporting/ip_list.py::to_ip_list`
only joins the two existing functions.

### CLI

`export()` in `reporting/__init__.py` gains an `ip-list` branch. `--format` choices on
`discover`, `check`, `run`, and the exhaustive path gain `ip-list`.

Output is strictly addresses and newlines. Nothing else is written to stdout in this
mode — no record count, no banner, no warnings — so the following is safe:

```
gaming discover --country IR --format ip-list > ips.txt
```

Diagnostics continue to go to stderr and the log, never stdout.

**Expansion bounds.** `expand_hosts` is bounded by design (`sample_per_range=16`,
`max_hosts=512`), and a country sweep can match millions of addresses. Silently emitting
512 of them would be a trap. Resolution: `ip-list` uses the current `Settings` values for
`sample_per_range` and `max_hosts`, and adds two explicit CLI overrides —
`--sample-per-range N` and `--max-hosts N`, where `0` means unbounded. When the cap
truncates the output, a note is written **to stderr** (never stdout, so the pipe stays
clean) stating how many addresses were emitted and that a cap applied.

**`watch` is excluded.** Watch mode is a long-running loop whose output is the history
database, not a stream of records; it has no single result set to format. Its discovered
addresses are retrieved with `gaming discover --country IR --exhaustive --format
ip-list`, or exported from the web panel per scan.

### Documentation of intent

The `--help` text for `--format` and both READMEs (English and Persian) will state
plainly that `ip-list` **discards all metadata by design**: no CIDR notation, no ASN, no
organization, no country. It is for users who want only the address list. It is
explicitly not a debugging or audit format — use `json` or `csv` for those.

### Web

`/api/export?kind=ip-list` joins `csv`, `json`, and `whitelist`, returning
`text/plain; charset=utf-8` as a download named `ips-scan-<id>.txt`. A matching
"Download IPs only (plain list)" button appears on the Live Scan and Search results
alongside the existing export buttons, calling the same `format_bare_ips` helper as
every other path.

### Tests

For both the CLI and the web export path, assert the output contains **only** valid bare
IP addresses: every line parses via `ipaddress.ip_address`, there is no leading or
trailing whitespace on any line, no header row, no CIDR notation (no `/` anywhere), and
no blank interior lines.

---

## Part 4 — Reverse IP lookup

### Lookup

`interactive/membership.py::find_matches(ip)` loads every stored `RangeEntry` across all
four categories (`iran_datacenter`, `iran_cdn`, `foreign_datacenter`, `foreign_cdn`) plus
custom and discovered entries, and returns every CIDR containing the address:

```python
ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
```

Results are sorted most-specific-first (descending prefix length) so nested and
overlapping ranges read sensibly — a `/32` before the `/22` that contains it. There can
legitimately be more than one match, and all are returned.

Each match carries its stored metadata: CIDR, category, origin, provider/organization,
and country, whichever of those are present. A malformed stored CIDR is skipped rather
than raising, so one bad line cannot break a lookup. An invalid input IP is a clean
validation error, not a traceback.

### Surfaces

* Web: `GET /api/lookup?ip=...`, driven by a small "IP lookup" box on the Search page,
  visually and functionally distinct from the existing CIDR/octet/provider search.
* CLI: `gaming check-membership <ip>`, honouring `--format` including `json`.

A new subcommand is preferable to a flag on `check`, whose existing semantics are
"probe these hosts". Membership lookup performs no network I/O in its default path;
overloading `check` would blur that.

### No match

The response says so plainly — "not found in any discovered/saved range" — rather than
returning an empty result the user has to interpret.

Optionally, and only on explicit user action, a one-click "run a discovery pass to check
this IP's real owner" performs a live RDAP/WHOIS lookup for that single address. It
reuses the existing discovery sources' request logic (`utils/http.py` plus the RDAP/WHOIS
parsing already in `discovery/`), not a new client. It runs as a `JobManager` job so it
cannot block the server.

### Tests

Covering both the web endpoint and the CLI path:

* Match found, single CIDR — correct CIDR and metadata returned.
* Match found, multiple overlapping/nested CIDRs — all returned, most-specific first.
* No match — the explicit "not found" response, not an empty success.
* Malformed input IP — clean error, no traceback.

---

## Cross-cutting

### Backward compatibility

* `custom_ranges.txt` written by earlier versions contains only `custom`/`discovered`
  origins and reads back unchanged.
* `settings.json` without the two new watch keys loads with defaults; watch is disabled
  when `watch_country` is empty.
* `NotFoundError` subclasses `HTTPError`, so every existing `except HTTPError` continues
  to catch it.
* `classify_category` and `persist_records` default their new flag to `False`, preserving
  current behaviour for the seeded path.
* The SQLite schema is unchanged. Watch mode writes ordinary scan rows.

### Verification per part

`python -m pytest` and the lint step run after each part, with before/after test counts
reported. Baseline is 332 passed, 1 skipped.

### Release

After all four parts are green:

1. `CHANGELOG.md` — one entry per part.
2. `README.md` — English and Persian, covering `--exhaustive`, `watch`, `ip-list`, and
   `check-membership`.
3. Version bump `0.8.0` → `0.9.0`.
4. Commit, push, and create a GitHub release with full notes in English and Persian.

If the push or release creation fails for missing authentication — no `gh` login, no git
remote credentials — stop and report exactly what is missing. Do not work around it.
