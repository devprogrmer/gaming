# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-07-30

This release comes out of an audit of the v0.9.0 README against real behaviour.
Six claimed capabilities were checked by running them, not by reading the code.
**Two of the six turned out to be already working as documented**; the audit is
reported honestly below, including where the original report of breakage was
wrong.

### Added
- **Live provider lookup by name** (`gaming discover --provider-name "<org>"`).
  Name-based discovery previously matched only against the bundled
  `providers.toml` seed list, so a real, registered company absent from that
  file returned nothing at all. Lookups now query the registries directly via
  RDAP entity search (ARIN `?name=`, RIPE `/entities?fn=` followed by the
  linked networks) and return CIDR, ASN, and country. A name matching several
  distinct organizations returns all of them. A name matching nothing reports
  that explicitly instead of returning silent emptiness.

  `--provider-name` is a **new, separate flag**: the existing `--provider`
  keeps its exact previous seed-list behaviour, so existing scripts do not
  change.
- **A dedicated provider lookup on all three surfaces.** The CLI flag above, a
  menu option, and a "Look up a provider by name" panel in the dashboard. All
  three call one shared function, so they cannot drift apart in what they find
  or in how they report failure.
- **Scan a specific CIDR from the web panel.** Every row on Search, provider
  lookup, and "what's new" now carries a *Scan* button that runs a reachability
  sweep scoped to that single range, plus a manual "type or paste a CIDR" input
  on Live Scan. Both reuse the existing `interactive/scanner.py` pipeline and
  `web/jobs.py` job polling rather than reimplementing scanning.
- **"What's new since your last visit."** The 24/7 watcher previously reported
  only *how many* ranges a cycle added, and that count was not stored anywhere
  — so returning after a week told you nothing about what had actually been
  found. Each cycle now diffs newly-discovered prefixes against what is already
  known and records them, with ASN, organization, and country, in a durable
  ledger.

  Surfaced as a menu banner plus menu option 11, `gaming watch --whats-new`,
  and an Overview panel in the dashboard. **Last-visited is tracked per
  surface**: reading the notice in the terminal does not clear it in the
  browser, so someone running the watcher on a server and checking both places
  does not silently lose one of the two reports. Reading never acknowledges —
  the stamp moves only when you have actually looked, and only as far as what
  was displayed, so a discovery arriving mid-read stays unread.
- **Recurring scans alongside the web panel** (`gaming web --schedule <scope>
  --schedule-interval <seconds>`), stopped as part of the same shutdown path.

### Fixed
- **Ctrl+C during a web-panel session: two real defects, but not the reported
  one.** The report was that Ctrl+C does not shut the panel down. Reproduced
  against a real process with a scan job in flight, **Ctrl+C already worked** —
  the v0.8.0 `ShutdownCoordinator` did what it claimed. Two narrower defects
  were real:

  1. `cmd_web` never passed a scheduler to `serve()`, so step 3 of the
     documented five-step shutdown sequence was a permanent no-op in
     production — there was never a scheduler for it to stop. There is now,
     via `--schedule`.
  2. Only `SIGINT` and `SIGTERM` were registered, so on Windows **Ctrl+Break
     bypassed the handler entirely**: measured exit `0xC000013A`, no cleanup
     output, port left bound. It now goes through the same single path —
     measured exit 0, full shutdown sequence, and an immediate rebind on the
     same port.
- **Latency was rendered at full binary precision** in the dashboard:
  `217.39859999979773` ms in a column a ping resolves to one decimal. Found by
  looking at the rendered page, not at the code. The unrounded value stays on
  the row, so sorting and CSV/JSON export are unaffected.
- **An empty results container painted a stray bordered sliver** under the
  panel that owned it — visible on Overview once "what's new" was acknowledged.
  It now collapses until it has rows or a placeholder to show.

### Audited, already correct
- **The dashboard's dark-mode visual system was already implemented.** The
  report listed it as outstanding. Verified by driving real Chrome through
  every view over the DevTools protocol: all CSS custom properties resolve,
  monospace is used for data and sans-serif for chrome, table headers are
  sticky, verdicts are pill badges, numeric columns are right-aligned,
  empty/loading/error states are styled, nav marks the active view, and the
  sidebar folds into a top nav below 820px with no horizontal overflow. The two
  rendering defects listed above were found during that pass and fixed.

## [0.9.0] - 2026-07-28

### Added
- **Exhaustive country-wide IP range discovery** (`gaming discover --country IR --exhaustive`).
  Queries RIR delegated-statistics files and resolves every allocated prefix via RIPEstat,
  RDAP, and WHOIS — surfacing real but obscure hosting companies with the same full detail
  (CIDR, ASN, organization, country) as well-known providers. Allocations with no public
  org name are kept and labelled `(unnamed / no public org name)` rather than dropped.
  Resumable across interruptions (journal persisted atomically), rate-limit friendly
  (429 exponential back-off, 404 fast-skip), and stored with a distinct
  `discovered_exhaustive` origin marker. Flags: `--exhaustive`, `--no-ipv6`,
  `--no-resume`, `--save`.
- **24/7 continuous watch mode** (`gaming watch --country IR --interval 1h`). Loops
  discovery → persist → scan → sleep indefinitely, reusing the existing daemon PID-file
  machinery so it survives SSH disconnect (`--daemon`/`--stop`/`--status`). Fail-soft:
  one bad iteration logs the error and continues. Configurable interval (`30m`, `2h`,
  `1d`, or bare seconds); minimum floor of 5 minutes to protect registries. Also
  startable and stoppable from the web dashboard (`POST /api/watch`).
- **`--format ip-list` bare-IP output.** Emits one host address per line with no
  metadata, safe for shell redirection (`gaming discover --format ip-list > ips.txt`).
  Progress, "saved", and "written:" messages go to stderr so stdout stays a pure IP
  list. Available on `discover`, `run`, and `check`; also exported from the web
  dashboard (`GET /api/export?kind=ip-list`).
- **Reverse IP membership lookup** (`gaming check-membership <ip>`). Checks an address
  against every stored CIDR across all categories using `ipaddress` membership; reports
  all matches (most specific first) with CIDR, group, origin, country, and provider.
  `--live` falls back to a live RDAP lookup when nothing stored matches. `--json` for
  machine-readable output. Exit codes: 0 = match found, 1 = not found, 2 = invalid IP.
  Also available in the web dashboard (`POST /api/lookup-ip`).

## [0.8.0] - 2026-07-26

This release is about **real Ctrl+C reliability** plus a **visual/UX overhaul**
across the web dashboard, the terminal UI, and the documentation.

### Fixed
- **Ctrl+C now actually shuts the web panel down cleanly — the 0.7.0 fix was
  incomplete.** 0.7.0 moved `serve_forever()` onto a background thread and
  wrapped the wait in `try/except KeyboardInterrupt`, which fixed the narrow
  case it was tested against but left three real failure modes, each verified
  by reproduction before this fix:

  1. **`SIGTERM` got no cleanup at all.** `gaming web --stop` signals the
     daemon with `SIGTERM` via the PID file, but `SIGTERM` does not raise
     `KeyboardInterrupt` — so the `except` clause never ran. The process was
     killed outright: no `shutdown()`, no `server_close()`, no PID-file
     removal. Measured: exit in 0.00s with cleanup skipped entirely.
  2. **In-flight scan jobs were abandoned mid-write.** `JobManager` exposed
     only `start`/`get` — there was no way to enumerate, cancel, or join job
     threads, and they were created `daemon=True`. On shutdown `serve()`
     returned while a scan thread was still running, and the interpreter then
     killed it at exit, potentially mid-SQLite-write. **This is the scenario
     behind the "panel just dies" report**: pressing Ctrl+C during a Live Scan.
  3. **A `KeyboardInterrupt` caught in one thread proves nothing about the
     others.** The interrupt is delivered at an arbitrary bytecode boundary in
     the main thread; catching it at a single call site said nothing about the
     scheduler or job threads still touching the database.

  Replaced with `gaming.web.lifecycle.ShutdownCoordinator`: a real
  `signal.signal()` handler for both `SIGINT` and `SIGTERM` that stops the
  listener from a separate thread (calling `shutdown()` from the
  `serve_forever()` thread deadlocks), cancels and bounded-joins in-flight job
  threads, stops the scan scheduler, releases the listening socket, removes the
  PID file, and only then prints a final `Web panel stopped.` The handler
  itself only sets a flag and returns — the multi-second drain happens on the
  waiting thread, never inside a signal handler.

  This is now the **single** shutdown path: `gaming web`, the interactive
  menu's "Launch web panel" option, and `daemon.stop()`'s `SIGTERM` all route
  through the same coordinator instead of three implementations that could
  drift apart again.
- **Background jobs are now cooperatively cancellable.** `Job.cancelled()`
  lets long-running workers stop at a safe point; the sequential scan loop
  polls it between CIDRs, so a shutdown mid-scan stops after the current CIDR
  and still persists what it completed instead of being killed. Jobs that
  ignore cancellation are bounded by a drain timeout and honestly reported
  (`N background job(s) did not stop in time`) rather than silently dropped.
- **An immediate restart on the same port works.** `server_close()` is now
  guaranteed to run, so the listening socket is released and a restart no
  longer risks "address already in use".
- **`serve()` can no longer hang if the serve loop exits on its own.** The
  loop now always releases the shutdown waiter in a `finally`, rather than
  only on the error path — a latent hang caught by the new tests.
- Repeated Ctrl+C escalates to an immediate exit (code 130) instead of leaving
  the user waiting, and the previous signal handlers are restored afterwards
  so the interactive menu keeps responding to Ctrl+C once the panel stops.
- `daemon.stop()`'s grace period was raised from 5s to 15s so a clean drain is
  not cut short by the `SIGKILL` escalation.

### Changed
- **Web dashboard visual overhaul.** Still stdlib-served with no build step, no
  CDN, and no webfonts — it renders identically on an air-gapped host.
  - A deliberate dark palette driven entirely by CSS custom properties in a
    single `:root` block. Nothing below that block hardcodes a colour, so the
    theme is retargetable from one place. One accent is used consistently for
    primary actions, the active nav item, and focus rings.
  - Monospace for operator data (hosts, CIDRs, ports, latency) and a sans stack
    for UI chrome, on a 4px spacing rhythm.
  - A persistent sidebar with a clear active-page indicator, plus a header
    showing session identity and live connection status; related controls are
    grouped into cards/panels instead of floating as bare form elements.
  - Tables gained sticky headers for long result sets, subtle row banding and
    hover, right-aligned tabular numerics for latency and node counts, and a
    sort caret on the active column. Status values render as pill badges with
    consistent colours across GOOD/MEDIUM/BAD and
    INTERNATIONAL/IRAN_ONLY/ABROAD_ONLY/UNREACHABLE.
  - Real empty, loading, and error states: placeholders that explain what to do
    next rather than a blank table, a determinate progress bar driven by the
    existing job-polling `progress` field (indeterminate until a fraction is
    known), and styled banners instead of raw error strings. A scan interrupted
    by shutdown now reports the new `cancelled` job status explicitly.
  - Responsive down to narrow desktop widths: the sidebar folds into a
    horizontal top nav rather than letting the content column collapse.
  - Honours `prefers-reduced-motion`.

  No endpoint, response shape, or behaviour changed — verified by the existing
  test suite plus headless-browser rendering of every view.
- **Terminal UI polish, on one shared renderer.** New
  `gaming.interactive.theme` holds the ANSI palette (as semantic roles —
  `title`, `prompt`, `muted`, `ok`, `warn`, `error`) and the single
  column-aligned table used across the whole terminal experience.
  - Replaces four separate ad-hoc `ljust` loops (three in `report.py`, plus
    per-command formatting in `sources` and the seed-check output), which had
    drifted apart in padding and header style.
  - Numeric columns (latency, loss, counts, scan IDs) are now right-aligned;
    verdict columns keep their existing colours.
  - The main menu, sub-menus, and prompts are styled coherently with the
    banner instead of the banner being the only coloured element.
  - `gaming sources` now prints a real table with a description per source
    (read from each source module's docstring) rather than a bare name list.
  - `gaming validate-seed` / `refresh-seeds` report stale CIDRs as a table with
    a styled pass/fail summary.

  All styling routes through the existing `_supports_color` predicate, so
  piped, redirected, `NO_COLOR`, and non-TTY output stays clean plain ASCII.
  A regression test asserts the invariant directly: **stripping ANSI from the
  coloured rendering yields byte-for-byte the plain rendering**, so colour can
  never disturb column alignment. Nothing added here animates or moves the
  cursor.
- **README restructured, and split into true parallel English/Persian
  versions.** Previously a single mixed-language file that was Persian-primary
  with stray English paragraphs; now `README.md` (English) and `README.fa.md`
  (Persian) carry identical structure and content, cross-linked at the top.
  - Bidirectional reachability, the web dashboard, and scheduled monitoring are
    described up front with their own sections instead of being single bullets
    buried in a feature list.
  - New "What the output actually looks like" section showing a real terminal
    results table, the live progress bar, and an ASCII mockup of the dashboard
    with a description of each page.
  - Consistent heading hierarchy plus a table of contents.
  - Every one of the 23 example commands was verified against the actual CLI
    (`--help` for each subcommand); the documented shutdown behaviour of
    `gaming web` was rewritten to match the fix above.
  - The "not a game" disclaimer and the responsible-use section are intact and
    prominent in both languages.




## [0.7.0] - 2026-07-22

### Added
- **Launch the web panel from the interactive menu.** The main menu (`gaming` /
  `gaming menu`) gained a new option, "Launch web panel", that starts the exact
  same dashboard server as `gaming web` in-process (no subprocess). It prompts
  for bind address / port / TLS the same way the CLI flags do, prints the
  identical first-run credentials/URL/security banner (the CLI subcommand and
  the menu action now both call the same `serve()` function — no duplicated
  banner logic), and returns control to the menu loop once the panel is
  stopped with Ctrl+C.
- **Live Scan: choose "Scan all together" vs "Scan one at a time".** The web
  dashboard's Live Scan page now offers an explicit choice before starting a
  scan. "Scan all together" is the original behavior — one combined job, one
  combined results table. "Scan one at a time" queues each matched CIDR as its
  own sequential step of a single background job: each CIDR's scan completes
  before the next starts, progress and results for each CIDR are reported
  separately (as soon as they're available, via the existing job-status
  polling — not just at the end), and a failure scanning one CIDR is recorded
  against that CIDR only, never aborting the rest of the queue. All CIDRs
  scanned in a given run — either mode — are still persisted as a single scan,
  so the existing "download results" / "download whitelist IPs" export
  buttons work identically regardless of which mode produced them.
- **Approximate "test path to..." proximity ping (RIPE Atlas).** A live IP's
  own outbound ping to an arbitrary third-party destination can't be measured
  by a remote tool — only the operator of that IP can make it originate
  traffic. As the closest honest approximation, `gaming.reachability.
  global_check.measure_from_near(source_ip, destination_ip)` looks up the
  source IP's origin ASN, finds a RIPE Atlas probe hosted in that same
  network (if any), and asks that probe to ping the destination. Wired into
  the web dashboard as an explicitly separate, opt-in "Test path to..." button
  on a scan row — never merged into the existing Iran/abroad bidirectional
  reachability columns, since it measures something conceptually different.
  Gated behind the existing `GAMING_RIPE_ATLAS_KEY` config (a no-op with a
  clear "not configured" message when unset); every result surfaced in the UI
  carries the disclaimer "Approximate — measured from the nearest available
  RIPE Atlas probe to this IP's network, not from the IP itself." A network
  with no nearby probe is reported as "no nearby probe available", never
  silently substituted with an unrelated probe.

### Fixed
- **Ctrl+C on `gaming web` (and the new menu launcher) now shuts down
  gracefully.** Previously the server caught `KeyboardInterrupt` around a
  same-thread `serve_forever()` and closed the listening socket immediately
  afterward — there was no guarantee the request-serving loop had actually
  finished. `serve_forever()` now runs on a background thread while the main
  thread waits on an event (interruptible by Ctrl+C independent of the
  server loop); on shutdown, `httpd.shutdown()` is called from the main
  thread (required — calling it from the same thread that's running
  `serve_forever()` deadlocks), the server thread is joined, and only then is
  the socket closed.

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

[Unreleased]: https://github.com/devprogrmer/gaming/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/devprogrmer/gaming/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/devprogrmer/gaming/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/devprogrmer/gaming/compare/v0.7.0...v0.8.0
[0.5.0]: https://github.com/devprogrmer/gaming/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/devprogrmer/gaming/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/devprogrmer/gaming/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/devprogrmer/gaming/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devprogrmer/gaming/releases/tag/v0.1.0
