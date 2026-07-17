"""The interactive, menu-driven experience.

This is the human-facing front end that ties everything together:

    * scan Iranian or foreign ranges with a live progress bar,
    * run a quick alive-discovery sweep,
    * browse persisted scan history,
    * manage (add/remove) custom IP ranges,
    * adjust classification thresholds and scan parameters.

It is deliberately simple: numbered menus and prompts, standard-library only,
and it never asks the user to run ``fping``, ``tail``, ``watch``, or ad-hoc
scripts. Everything happens inside the loop.
"""

from __future__ import annotations

import ipaddress
import sys
from collections.abc import Iterable
from typing import TextIO

from .. import __version__
from . import ranges as ranges_mod
from . import report as report_mod
from . import scanner
from .classify import GOOD
from .progress import ProgressBar
from .settings import Settings, load_settings, save_settings
from .storage import HistoryStore


def parse_first_octets(input_str: str) -> list[int]:
    """Parse a comma-separated string of first octets into a validated list.

    Each token must be an integer in the range 0-255. Blank tokens are
    ignored, duplicates are removed (order preserved). Raises ``ValueError``
    if any token is non-numeric or out of range, so the caller can show the
    offending value to the user.
    """
    octets: list[int] = []
    seen: set[int] = set()
    for token in input_str.split(","):
        text = token.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a number") from None
        if not 0 <= value <= 255:
            raise ValueError(f"{value} is out of range (0-255)")
        if value not in seen:
            seen.add(value)
            octets.append(value)
    return octets


def matches_first_octet(prefix: str, allowed_octets: Iterable[int]) -> bool:
    """Return True if the first octet of ``prefix`` is in ``allowed_octets``.

    ``prefix`` may be a CIDR (``185.51.200.0/22``) or a bare IP. IPv6 prefixes
    have no dotted first octet and never match. Malformed prefixes are treated
    as non-matching rather than raising, so a single bad record can't abort a
    whole filtering pass.
    """
    allowed = set(allowed_octets)
    if not allowed:
        return True
    try:
        net = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return False
    if net.version != 4:
        return False
    first_octet = int(net.network_address) >> 24 & 0xFF
    return first_octet in allowed


def format_bare_ips(hosts: Iterable[str]) -> str:
    """Render hosts as a copy-paste-ready block: one bare IP per line.

    De-duplicates while preserving order and strips whitespace. Produces no
    prefixes, symbols, colours, or headers — just the addresses — so the output
    can be piped or pasted straight into another tool. Returns an empty string
    when there are no hosts.
    """
    seen: set[str] = set()
    out: list[str] = []
    for host in hosts:
        ip = host.strip()
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return "\n".join(out)


# Category scan definitions: key -> (menu label, predicate name in
# gaming.processing.filters). The predicate enforces the separation between
# datacenter-only, foreign-CDN, and Iranian-CDN targets in the actual filtering
# step (not just the displayed label).
_CATEGORY_LABELS = {
    "datacenter": "Datacenter",
    "foreign_cdn": "Foreign CDN/Cloud",
    "iran_cdn": "Iranian CDN",
}

_REGION_LABELS = {
    "middle_east": "Middle East",
    "europe": "Europe",
    "asia": "Asia",
    "all": "All supported regions",
}

_MENU = """
==================================================
  gaming — IP Health Scanner  (v{version})
==================================================
  1) Scan Iranian IP ranges
  2) Scan foreign IP ranges
  3) Discover alive IPs (quick sweep)
  4) View scan history
  5) Manage IP ranges
  6) Settings
  7) Update installed version
  8) Filter CIDRs by first octet
  9) Scan Datacenters
 10) Scan Foreign CDN/Cloud Providers
 11) Scan Iranian CDN Providers
  0) Exit
--------------------------------------------------"""


class Menu:
    """Interactive menu loop with injectable I/O streams (testable)."""

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        store: HistoryStore | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.store = store or HistoryStore()
        self.settings: Settings = load_settings()

    # ---- I/O helpers -----------------------------------------------------
    def _print(self, text: str = "") -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()

    def _prompt(self, message: str) -> str:
        self.stdout.write(message)
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:  # EOF (e.g. piped input exhausted) — behave like "exit".
            raise EOFError
        return line.strip()

    # ---- main loop -------------------------------------------------------
    def run(self) -> int:
        self.store.initialize()
        while True:
            self._print(_MENU.format(version=__version__))
            try:
                choice = self._prompt("Select an option: ")
            except EOFError:
                self._print("\nGoodbye.")
                return 0

            try:
                if choice == "1":
                    self._scan("iran")
                elif choice == "2":
                    self._scan("foreign")
                elif choice == "3":
                    self._discover_alive()
                elif choice == "4":
                    self._history()
                elif choice == "5":
                    self._manage_ranges()
                elif choice == "6":
                    self._settings()
                elif choice == "7":
                    self._update_installed_version()
                elif choice == "8":
                    self._filter_by_first_octet()
                elif choice == "9":
                    self._scan_category("datacenter")
                elif choice == "10":
                    self._scan_category("foreign_cdn")
                elif choice == "11":
                    self._scan_category("iran_cdn")
                elif choice in ("0", "q", "quit", "exit"):
                    self._print("Goodbye.")
                    return 0
                else:
                    self._print("Unknown option. Please choose from the menu.")
            except EOFError:
                self._print("\nGoodbye.")
                return 0
            except KeyboardInterrupt:
                self._print("\nCancelled.")

    # ---- actions ---------------------------------------------------------
    def _scan(self, scope: str) -> None:
        cidrs = ranges_mod.load_ranges(scope)
        hosts = ranges_mod.expand_hosts(
            cidrs,
            sample_per_range=self.settings.sample_per_range,
            max_hosts=self.settings.max_hosts,
        )
        if not hosts:
            self._print(
                f"No {scope} ranges configured. Add some from 'Manage IP ranges'."
            )
            return

        self._print(
            f"\nScanning {len(hosts)} host(s) from {len(cidrs)} {scope} range(s) "
            f"({self.settings.ping_count} probe(s) each)...\n"
        )
        bar = ProgressBar(len(hosts), stream=self.stdout, label=f"{scope.title()} scan")

        def _hook(_probe, verdict: str) -> None:
            bar.update(verdict)

        report = scanner.run_scan(scope, self.settings, on_result=_hook, hosts=hosts)
        bar.finish()

        scan_id = scanner.persist(report, self.store)
        self._print("")
        self._print(report_mod.render_report(report, stream=self.stdout))
        self._print(
            report_mod.summary_line(report.counts, report.total, stream=self.stdout)
        )
        self._print(f"Saved as scan #{scan_id}. Use 'View scan history' to revisit it.")

    def _discover_alive(self) -> None:
        scope = self._prompt("Scope to sweep [iran/foreign] (default iran): ").lower()
        if scope not in ranges_mod.SCOPES:
            scope = "iran"
        cidrs = ranges_mod.load_ranges(scope)
        hosts = ranges_mod.expand_hosts(
            cidrs,
            sample_per_range=self.settings.sample_per_range,
            max_hosts=self.settings.max_hosts,
        )
        if not hosts:
            self._print(f"No {scope} ranges configured.")
            return

        self._print(f"\nSweeping {len(hosts)} host(s) for signs of life...\n")
        bar = ProgressBar(len(hosts), stream=self.stdout, label="Alive sweep")

        def _hook(probe) -> None:
            bar.update("GOOD" if probe.reachable else "BAD")

        alive = scanner.discover_alive(scope, self.settings, on_result=_hook, hosts=hosts)
        bar.finish()

        self._print("")
        if not alive:
            self._print("No alive hosts found in the sampled ranges.")
            return
        self._print(f"Found {len(alive)} alive host(s):")
        for host in alive:
            self._print(f"  {host}")

        answer = self._prompt(
            "\nRun a full health scan on these alive hosts now? [y/N]: "
        ).lower()
        if answer in ("y", "yes"):
            self._full_scan_hosts(scope, alive)

    def _full_scan_hosts(self, scope: str, hosts: list[str]) -> None:
        self._print(f"\nRunning a full health scan on {len(hosts)} alive host(s)...\n")
        bar = ProgressBar(len(hosts), stream=self.stdout, label="Health scan")

        def _hook(_probe, verdict: str) -> None:
            bar.update(verdict)

        report = scanner.run_scan(
            scope, self.settings, on_result=_hook, hosts=hosts
        )
        bar.finish()
        scan_id = scanner.persist(report, self.store)
        self._print("")
        self._print(report_mod.render_report(report, stream=self.stdout))
        self._print(
            report_mod.summary_line(report.counts, report.total, stream=self.stdout)
        )
        self._print(f"Saved as scan #{scan_id}.")

    def _history(self) -> None:
        scans = self.store.list_scans(limit=20)
        self._print("")
        self._print(report_mod.render_history(scans))
        if not scans:
            return
        answer = self._prompt(
            "Enter a scan ID to view details (blank to return): "
        ).strip()
        if not answer:
            return
        try:
            scan_id = int(answer)
        except ValueError:
            self._print("Not a valid scan ID.")
            return
        rows = self.store.get_results(scan_id)
        if not rows:
            self._print(f"No results found for scan #{scan_id}.")
            return
        self._print("")
        self._print(report_mod.render_results(rows, stream=self.stdout, limit=50))

    def _manage_ranges(self) -> None:
        while True:
            self._print(
                "\nManage IP ranges\n"
                "  1) List Iranian ranges\n"
                "  2) List foreign ranges\n"
                "  3) Add a custom range\n"
                "  4) Remove a custom range\n"
                "  0) Back"
            )
            choice = self._prompt("Select: ")
            if choice == "1":
                self._list_ranges("iran")
            elif choice == "2":
                self._list_ranges("foreign")
            elif choice == "3":
                self._add_range()
            elif choice == "4":
                self._remove_range()
            elif choice in ("0", ""):
                return
            else:
                self._print("Unknown option.")

    def _list_ranges(self, scope: str) -> None:
        all_ranges = ranges_mod.load_ranges(scope)
        custom = set(ranges_mod.custom_ranges(scope))
        self._print(f"\n{scope.title()} ranges ({len(all_ranges)} total):")
        for cidr in all_ranges:
            tag = " [custom]" if cidr in custom else ""
            self._print(f"  {cidr}{tag}")

    def _add_range(self) -> None:
        scope = self._prompt("Scope [iran/foreign]: ").lower()
        if scope not in ranges_mod.SCOPES:
            self._print("Scope must be 'iran' or 'foreign'.")
            return
        cidr = self._prompt("CIDR to add (e.g. 185.51.200.0/22): ")
        try:
            normalized = ranges_mod.add_custom_range(scope, cidr)
        except ValueError as exc:
            self._print(f"Could not add range: {exc}")
            return
        self._print(f"Added {normalized} to {scope} ranges.")

    def _remove_range(self) -> None:
        scope = self._prompt("Scope [iran/foreign]: ").lower()
        if scope not in ranges_mod.SCOPES:
            self._print("Scope must be 'iran' or 'foreign'.")
            return
        custom = ranges_mod.custom_ranges(scope)
        if not custom:
            self._print(f"No custom {scope} ranges to remove.")
            return
        self._print(f"Custom {scope} ranges:")
        for cidr in custom:
            self._print(f"  {cidr}")
        cidr = self._prompt("CIDR to remove: ")
        if ranges_mod.remove_custom_range(scope, cidr):
            self._print(f"Removed {cidr}.")
        else:
            self._print("That range was not found among custom ranges.")

    def _settings(self) -> None:
        fields = [
            ("good_latency_ms", "GOOD latency threshold (ms)"),
            ("good_loss_pct", "GOOD loss threshold (%)"),
            ("medium_latency_ms", "MEDIUM latency threshold (ms)"),
            ("medium_loss_pct", "MEDIUM loss threshold (%)"),
            ("ping_count", "Probes per host"),
            ("concurrency", "Concurrency"),
            ("timeout", "Per-probe timeout (s)"),
            ("sample_per_range", "Hosts sampled per range"),
            ("max_hosts", "Max hosts per scan"),
        ]
        while True:
            self._print("\nSettings (blank keeps current value):")
            for i, (attr, label) in enumerate(fields, start=1):
                self._print(f"  {i}) {label}: {getattr(self.settings, attr)}")
            self._print("  s) Save    r) Reset to defaults    0) Back")
            choice = self._prompt("Select: ").lower()
            if choice in ("0", ""):
                return
            if choice == "s":
                save_settings(self.settings)
                self._print("Settings saved.")
                continue
            if choice == "r":
                self.settings = Settings()
                self._print("Settings reset to defaults (not yet saved).")
                continue
            if not choice.isdigit() or not (1 <= int(choice) <= len(fields)):
                self._print("Unknown option.")
                continue
            attr, label = fields[int(choice) - 1]
            self._edit_setting(attr, label)

    def _edit_setting(self, attr: str, label: str) -> None:
        current = getattr(self.settings, attr)
        raw = self._prompt(f"{label} [{current}]: ").strip()
        if not raw:
            return
        try:
            value: float | int = int(raw) if isinstance(current, int) else float(raw)
        except ValueError:
            self._print("Please enter a number.")
            return
        setattr(self.settings, attr, value)
        self.settings = self.settings.clamped()
        self._print(f"Set {label} to {getattr(self.settings, attr)}.")

    def _select_region(self) -> str | None:
        """Prompt for a scan region. Returns a region key or None if cancelled."""
        choice = self._prompt(
            "\nSelect region:\n"
            "  1) Middle East\n"
            "  2) Europe\n"
            "  3) Asia\n"
            "  4) All supported regions\n"
            "Choice (default 4): "
        ).strip()
        mapping = {"1": "middle_east", "2": "europe", "3": "asia", "4": "all"}
        if choice == "":
            return "all"
        region = mapping.get(choice)
        if region is None:
            self._print("Unknown region option.")
        return region

    def _scan_category(self, category: str) -> None:
        """Discover, filter to a single category + region, then scan.

        The three categories are mutually exclusive by construction — each uses
        its own predicate from :mod:`gaming.processing.filters`:

        * ``datacenter``   → :func:`is_datacenter_only` (excludes CDN/cloud/edge)
        * ``foreign_cdn``  → :func:`is_foreign_cdn` (Cloudflare/Fastly/Meta/…)
        * ``iran_cdn``     → :func:`is_iranian_cdn`

        so a record can never leak from one scan flow into another. A region
        selection is then applied to narrow which CIDRs reach the scanner.
        """
        from ..processing.filters import (
            is_datacenter_only,
            is_foreign_cdn,
            is_iranian_cdn,
            matches_region,
        )

        predicates = {
            "datacenter": is_datacenter_only,
            "foreign_cdn": is_foreign_cdn,
            "iran_cdn": is_iranian_cdn,
        }
        predicate = predicates[category]
        label = _CATEGORY_LABELS[category]

        region = self._select_region()
        if region is None:
            return

        # Imported lazily so launching the menu doesn't pull in the whole
        # discovery/reachability stack unless a scan is actually run.
        from .. import pipeline
        from ..config import load_config
        from ..models import Filters

        # Iranian-CDN scans scope discovery to Iran so unrelated foreign records
        # never enter the flow; the other categories rely on their predicate.
        countries = ["IR"] if category == "iran_cdn" else []
        config = load_config()
        filters = Filters(countries=countries)

        self._print(f"\n[{label}] Discovering IP ranges...")
        raw = pipeline.discover(config, filters)
        records = pipeline.process(raw, filters)

        # Enforce category separation in the actual filtering, then the region.
        matched = [r for r in records if predicate(r)]
        matched = [r for r in matched if matches_region(r, region)]

        region_label = _REGION_LABELS.get(region, region)
        self._print(
            f"\n{len(matched)} of {len(records)} discovered record(s) match "
            f"{label} in {region_label}:"
        )
        if not matched:
            self._print(
                "  (none) — no matching CIDRs for this category/region."
            )
            return
        for rec in matched:
            country_tag = f" [{rec.country}]" if rec.country else ""
            org_tag = f" — {rec.organization}" if rec.organization else ""
            self._print(f"  {rec.prefix}{country_tag}{org_tag}")

        self._auto_scan_matched(matched, scope=category)

    def _update_installed_version(self) -> None:
        """Upgrade or switch the installation in place from the interactive menu.

        Reuses the same :func:`gaming.updater.run_update` flow as the ``gaming
        update`` CLI subcommand, so user state (scan history, settings, custom
        ranges) — which lives outside the install tree — is preserved. When an
        in-place update isn't possible, the updater's error message carries the
        safest manual fallback.
        """
        from ..updater import UpdateError, list_releases, run_update

        self._print(f"\nCurrent installed version: {__version__}")

        try:
            releases = list_releases()
        except UpdateError as exc:
            self._print(f"Update failed: {exc}")
            return
        if releases:
            self._print("Available releases (newest first): " + ", ".join(releases))

        target = self._prompt(
            "\nEnter a release to switch to (e.g. v0.1.0), "
            "or blank to update to the latest: "
        ).strip()
        ref = target or None

        try:
            result = run_update(ref=ref, log=self._print)
        except UpdateError as exc:
            self._print(f"\nUpdate failed: {exc}")
            return

        if result.ref is not None:
            self._print(
                f"\nSwitched gaming {result.previous_version} -> "
                f"{result.new_version} (release {result.ref})."
            )
        elif result.changed:
            self._print(
                f"\nUpdated gaming {result.previous_version} -> {result.new_version}."
            )
        else:
            self._print(
                f"\ngaming is already up to date (version {result.new_version})."
            )
        self._print("Restart gaming for the new version to take effect.")

    def _filter_by_first_octet(self) -> None:
        """Discover ranges, then filter the records by first octet + datacenter.

        This layers dynamic filters on top of the existing discovery filters
        (e.g. ``--country``): discovery + the standard ``Filters`` run first,
        then the first-octet predicate and the datacenter choice are applied to
        the resulting ``records`` list.
        """
        raw_input = self._prompt("Enter first octet(s) (0-255, comma-separated): ")
        try:
            allowed = parse_first_octets(raw_input)
        except ValueError as exc:
            self._print(f"Invalid input: {exc}")
            return
        if not allowed:
            self._print("No octets provided. Nothing to filter.")
            return

        # Step 2: datacenter filtering.
        dc_choice = self._prompt(
            "\nFilter by datacenter?\n"
            "  1) All datacenters\n"
            "  2) Specific datacenter/provider/org\n"
            "Choice: "
        ).strip()
        provider_term = ""
        if dc_choice == "1":
            dc_mode = "all"
        elif dc_choice == "2":
            dc_mode = "specific"
            provider_term = self._prompt(
                "Enter datacenter/provider/org name: "
            ).strip()
            if not provider_term:
                self._print("No name provided. Nothing to filter.")
                return
        else:
            self._print("Unknown option. Please choose 1 or 2.")
            return

        # Optional country filter so the dynamic filters demonstrably work
        # alongside the existing discovery filters.
        country = self._prompt(
            "Optional country filter (e.g. IR,DE; blank for none): "
        ).strip()

        # Imported lazily so launching the menu doesn't pull in the whole
        # discovery/reachability stack unless this feature is actually used.
        from .. import pipeline
        from ..config import load_config
        from ..models import Filters
        from ..processing.filters import is_datacenter

        countries = [c.strip() for c in country.split(",") if c.strip()]
        # A "specific" datacenter/provider/org term reuses the existing provider
        # filter, so it is applied natively during process() alongside country.
        providers = [provider_term] if dc_mode == "specific" else []
        config = load_config()
        filters = Filters(countries=countries, providers=providers)

        self._print("\nDiscovering IP ranges...")
        raw = pipeline.discover(config, filters)
        records = pipeline.process(raw, filters)

        # Apply the dynamic filters on top of the existing filters.
        matched = [r for r in records if matches_first_octet(r.prefix, allowed)]
        if dc_mode == "all":
            matched = [r for r in matched if is_datacenter(r)]

        octet_label = ", ".join(str(o) for o in allowed)
        if dc_mode == "all":
            dc_label = "all datacenters"
        else:
            dc_label = f"provider/org ~ {provider_term!r}"
        self._print(
            f"\n{len(matched)} of {len(records)} discovered record(s) match "
            f"first octet [{octet_label}] and {dc_label}:"
        )
        if not matched:
            self._print("  (none)")
            return
        for rec in matched:
            country_tag = f" [{rec.country}]" if rec.country else ""
            org_tag = f" — {rec.organization}" if rec.organization else ""
            self._print(f"  {rec.prefix}{country_tag}{org_tag}")

        # Step 3: auto-scan the filtered records with strict reachability +
        # location requirements, then persist the qualifying hosts.
        self._auto_scan_matched(matched)

    def _auto_scan_matched(self, records: list, scope: str = "filtered") -> None:
        """Health-scan the filtered records and keep only qualifying hosts.

        Two strict requirements gate the final list:

        * **Strict reachability** — a host must classify as ``GOOD`` (a real,
          low-latency, low-loss reply). ``MEDIUM`` and ``BAD`` are rejected.
        * **Location requirement** — the record must carry a verified country
          code, so results without a known location are excluded.

        Qualifying hosts are saved to scan history for later review, and their
        addresses are printed as a clean, copy-paste-ready bare-IP block.
        """
        # Only scan records that already satisfy the location requirement; a
        # record with no country can never qualify, so probing it is wasted work.
        located = [r for r in records if r.country]
        skipped_no_location = len(records) - len(located)
        if skipped_no_location:
            self._print(
                f"\nExcluding {skipped_no_location} record(s) with no known "
                f"location (location requirement)."
            )
        if not located:
            self._print("No records meet the location requirement. Nothing to scan.")
            return

        # Map each probe host back to its record so we can report location.
        host_to_record: dict[str, object] = {}
        for rec in located:
            host_to_record[rec.sample_host()] = rec
        hosts = list(host_to_record)

        self._print(
            f"\nAuto-scanning {len(hosts)} located host(s) "
            f"({self.settings.ping_count} probe(s) each, strict GOOD only)...\n"
        )
        bar = ProgressBar(len(hosts), stream=self.stdout, label="Auto-scan")

        def _hook(_probe, verdict: str) -> None:
            bar.update(verdict)

        report = scanner.run_scan(
            scope, self.settings, on_result=_hook, hosts=hosts
        )
        bar.finish()

        # Strict reachability: keep only hosts classified GOOD.
        qualifying = [
            (probe, host_to_record[probe.host])
            for probe, verdict in report.results
            if verdict == GOOD and probe.host in host_to_record
        ]

        scan_id = scanner.persist(report, self.store)
        self._print("")
        self._print(
            report_mod.summary_line(report.counts, report.total, stream=self.stdout)
        )
        self._print(
            f"\n{len(qualifying)} host(s) meet both strict reachability (GOOD) "
            f"and the location requirement:"
        )
        if not qualifying:
            self._print("  (none)")
        else:
            for probe, rec in qualifying:
                latency = f"{probe.avg_ms:.0f} ms" if probe.avg_ms is not None else "?"
                self._print(f"  {probe.host}  [{rec.country}]  {latency}")

        # Clean, copy-paste-ready output: bare alive IPs, one per line, no
        # colours/symbols/prefixes. Empty section stays friendly.
        self._print("\n--- Alive IPs (copy-paste ready) ---")
        if qualifying:
            self._print(format_bare_ips(probe.host for probe, _rec in qualifying))
        else:
            self._print("(no live IPs found)")
        self._print(f"\nSaved as scan #{scan_id}. Use 'View scan history' to revisit it.")


def run(argv: list[str] | None = None) -> int:
    """Entry point for the interactive menu."""
    return Menu().run()
