# README v0.9.0 audit — fixing the real gaps

**Date:** 2026-07-29
**Status:** approved, ready for implementation

## Context

The README documents six features as working. Real-world server use suggested
most of them did not. Rather than trust the README, the CHANGELOG, or the commit
messages, every claim was verified by running the code. Two claims turned out to
be accurate; four were genuinely broken. This document records what was measured
and what will be built.

## Audit results

### Item 1 — Ctrl+C shutdown of the web panel: WORKS

Measured by spawning real `gaming web` subprocesses and delivering a genuine
`SIGINT` (via `signal.raise_signal` inside the child, since Windows cannot
target `CTRL_C_EVENT` at one child process):

| Scenario | Result |
| --- | --- |
| Plain foreground SIGINT | exit 0 in 0.80s, socket rebinds |
| SIGINT + idle keep-alive connection held open | exit 0, socket rebinds |
| SIGINT + authenticated `/api/scan` job in flight | exit 0, socket rebinds |

In each case the child printed `Received SIGINT — stopping the web panel...`
followed by `Web panel stopped.`. `ShutdownCoordinator` in `web/lifecycle.py` is
genuinely the single shutdown path, shared by `gaming web`, the interactive menu
(`interactive/actions/web_action.py` calls the same `serve()`), and `--stop`
via `SIGTERM`.

Two real but narrower defects were found:

1. `cli.py` never passes `scheduler=` to `serve()`, so step 3 of the documented
   five-step shutdown sequence is a permanent no-op in production.
2. Only `SIGINT` and `SIGTERM` are registered. Windows terminals send
   `CTRL_BREAK_EVENT` on Ctrl+Break, which bypasses the handler entirely — the
   process dies with exit code `3221225786` (`STATUS_CONTROL_C_EXIT`), running no
   cleanup and printing nothing.

### Item 6 — dashboard visual polish: ALREADY IMPLEMENTED

`web/static/app.css` (711 lines) already contains the complete `:root` token
block (`--accent`, `--bg`, `--surface`, `--text`, `--danger`, `--warn`, `--ok`),
a 4px spacing scale, `--mono` for data and `--sans` for chrome, a persistent
sidebar with an `.active` state, pill `.badge` classes, `thead th { position:
sticky }`, alternating row shading, `td.num` right-alignment, and styled
`.empty-state` / `.banner` / `.spinner` / `.progress` blocks. `app.js` uses all
of it: `renderTable` honours per-column `num`/`badge`/`action`, and
`emptyState`, `banner`, and `setStatus` are wired throughout. Assets are bundled
and served correctly.

Remaining work is a real browser inspection to confirm this renders as the
source suggests, plus styling any new UI from Items 2/4/5 consistently.

### Item 3 — named-provider discovery returns nothing: BROKEN

No code path anywhere queries live registry data by organization name. A
supplied name is only ever a lowercased substring filter applied *after* records
have been fetched by ASN or country (`processing/filters.py:294-298`, duplicated
at `interactive/filters_shared.py:105-108`). Every source under `discovery/`
returns `[]` without ASN seeds (`rdap.py:38-40`, `asn_bgp.py:21-23`), and
`rir.py:110` explicitly sets `organization=None`, so a country sweep produces
records that no provider substring can ever match.

Measured:

```
discover --provider "Zenlayer" --country IR  ->  2532 raw -> 0 records -> "No records."
discover --provider "Zenlayer"               ->    12 raw -> 0 records -> "No records."
```

Zenlayer is a real registered company, absent from the 42-entry
`interactive/data/providers.toml`. Separately, `_DATACENTER_KEYWORDS` in
`processing/filters.py` would drop a legitimate org whose name lacks a hosting
keyword from any category-scoped path.

### Item 4 — on-demand named lookup: MISSING

`--provider` and `--org` exist but are the broken substring filters above. There
is no menu prompt, no web input, and no shared lookup function.

### Item 2 — scan a specific CIDR from the web panel: MISSING (backend ready)

`POST /api/scan` already accepts `{"cidrs": [...]}` and `handlers.py:358` honours
an explicit list before falling back to stored categories. The browser never
populates it: `app.js:308-310` posts only `{category, mode}`, `index.html:86-98`
has no CIDR input, and search rows have no action column. "Scan one at a time"
iterates a whole category rather than a user-chosen range. The endpoint also
performs no validation of caller-supplied CIDRs.

### Item 5 — "what's new" reporting: MISSING

`WatchLoop.run_forever` genuinely runs and persists discovered ranges, but
`ranges.persist_exhaustive_records:275` returns `{category: count}` and discards
the actual new CIDRs. `WatchState.last_persisted` is in-memory and lost on
restart. The only diff in the codebase, `alerts.diff_last_two:56-86`, compares
reachability verdicts of hosts present in *both* scans and explicitly ignores
appearance and disappearance. There are no last-visited timestamps anywhere and
nothing distinguishing the menu from the web. `gaming watch` has no menu entry
and `/api/watch` is unreachable from the UI.

## Design

### Provider name lookup (Items 3 and 4)

A new module, `discovery/provider_lookup.py`, exposing one function:

```python
def lookup_provider_by_name(name, *, timeout, limit=200) -> ProviderLookupResult
```

Two RDAP query shapes are required, because no single one covers both
registries. Verified live against the real endpoints:

| Registry | Query | Result for "Zenlayer" |
| --- | --- | --- |
| ARIN | `/registry/ips?name=Zenlayer*` | 177 networks, each with `cidr0_cidrs` |
| RIPE | `/entities?fn=Zenlayer` then `/entity/<handle>` | 12 orgs; `ORG-ZI112-RIPE` ("Zenlayer Inc.") yields 2 networks with `cc=DE` |
| APNIC / LACNIC / AFRINIC | either shape | 0 results or HTTP 404 — no usable name search |

RIPE rejects mid-string wildcards (`*Zenlayer*` returns HTTP 500) and its
`/ips?name=` endpoint also returns HTTP 500, so the entity-then-follow path is
the only viable one there. ARIN returns a clean HTTP 404 for a name with no
matches, which the project's existing `utils.http.get_json` already raises as
`HTTPError` — that is what keeps "no such provider" distinguishable from
"registry unavailable".

Each network hit carries `cidr0_cidrs` entries of the form
`{"v4prefix": "62.115.250.0", "length": 24}`, converted to a normal `IPRecord`
with `source="rdap-name"`. Results are deduplicated by prefix and capped by
`limit`.

Results deliberately **bypass** the provider-substring filter and the
`_DATACENTER_KEYWORDS` classifier. The user named the organization explicitly;
re-filtering by keyword is precisely what silently emptied the result set in the
first place.

The result object distinguishes three outcomes so every surface can report them
plainly: matches found, no such organization, and registry lookup failed.

Three surfaces, one function:

- **CLI:** `gaming discover --provider-name "Exact Company Name"`. A *new* flag.
  `--provider` and `--org` keep their current substring-filter semantics so
  anything already scripted against them continues to work.
- **Menu:** a prompt for the name, reusing the shared formatter.
- **Web:** `POST /api/provider-lookup` behind a "Look up a provider by name"
  input on Search, distinct from the existing CIDR/octet inputs.

### Per-CIDR scanning from the web (Item 2)

The backend already supports this, so the work is UI plus validation:

- a CIDR text input in the Live Scan panel,
- a "Scan this CIDR" action column on Search result rows,
- both posting `{cidrs: [x], mode}` through the existing `pollJob` polling path
  and rendering into the same results table,
- server-side validation of caller-supplied CIDRs via `ipaddress`, rejecting
  malformed input with a clear error rather than failing deep in the scanner.

No new scan implementation: this reuses `interactive/scanner.py` through the
existing `web/jobs.py` job machinery.

### Discovery diffing and "what's new" (Item 5)

`ranges.persist_exhaustive_records` changes to return the prefixes it actually
inserted rather than a count, which is the piece of information currently thrown
away.

Two additive SQLite tables (existing databases keep working; the schema helper
already adds columns additively):

- `discoveries(prefix, asn, org, country, first_seen)` — one row per
  newly-observed range,
- `surface_visits(surface, last_visited)` — one row per surface, keyed by
  `"menu"` or `"web"`.

`WatchLoop._persist` records genuinely-new rows. Each surface asks for
`discoveries` newer than *its own* `last_visited`, so viewing the web dashboard
does not clear the menu's notice and vice versa. The menu shows a startup banner
("N new ranges discovered since your last visit") with a detail view; the web
gets an Overview panel with view and export of only the new entries. When there
is nothing new, both say so plainly rather than rendering an empty table.

This reuses the existing daemon mechanism shared by `gaming web` and
`gaming watch` — no second daemonization path.

### Item 1 fixes

- Pass `scheduler=` from `cli.py`'s `cmd_web` into `serve()` so the documented
  step 3 actually runs.
- Register `SIGBREAK` alongside `SIGINT`/`SIGTERM` in
  `ShutdownCoordinator._signals`, guarded by `getattr` since the signal only
  exists on Windows, so Ctrl+Break stops hard-killing the process.

## Testing

- seeded provider still resolves (no regression),
- unseeded realistic provider against mocked ARIN and RIPE JSON returns CIDRs,
  ASN, and country,
- nonsense name returns an explicit "not found", not silent emptiness,
- a name matching several distinct organizations returns all of them,
- registry failure is reported as a failure, not as "no results",
- a web endpoint test scanning one specified CIDR, distinct from the bulk
  category scan,
- malformed CIDR input to `/api/scan` is rejected with a clear error,
- a watch cycle mixing known and new ranges identifies only the new ones,
- "no new entries" reports plainly,
- last-visited timestamps update correctly and independently per surface.

## Constraints

Standard library only; no new runtime dependencies; no frontend build tooling.
Fail-soft behaviour and backward compatibility with existing on-disk data,
settings, and config are preserved throughout. Every item is verified by running
it, not by reading the diff.
