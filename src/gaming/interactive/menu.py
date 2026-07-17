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
from .progress import ProgressBar, _supports_color
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


# Origin (Iran vs foreign) and class (datacenter vs cdn) map onto the four
# storage categories in gaming.interactive.ranges.
_ORIGIN_LABELS = {"iran": "Iran", "foreign": "Foreign"}
_CLASS_LABELS = {"datacenter": "Datacenter", "cdn": "CDN / Cloud"}


def _categories_for(origin: str, cls: str) -> list[str]:
    """Resolve an (origin, class) selection to storage category keys."""
    classes = ("datacenter", "cdn") if cls == "both" else (cls,)
    origins = ("iran", "foreign") if origin == "both" else (origin,)
    return [f"{o}_{c}" for o in origins for c in classes]


_BANNER = r"""
      _
   __| | _____   ___ __  _ __ ___   __ _ _ __ ___  ___ _ __
  / _` |/ _ \ \ / / '_ \| '__/ _ \ / _` | '__/ _ \/ _ \ '__|
 | (_| |  __/\ V /| |_) | | | (_) | (_| | | |  __/  __/ |
  \__,_|\___| \_/ | .__/|_|  \___/ \__, |_|  \___|\___|_|
                  |_|              |___/
"""


def render_banner(stream: TextIO | None = None) -> str:
    """Return the ``devprogrmer`` banner, coloured on a capable TTY.

    Falls back to plain ASCII (no escape codes) when the stream is not an
    ANSI-capable TTY, so it stays clean over SSH, in pipes, and in CI.
    """
    stream = stream or sys.stdout
    art = _BANNER.rstrip("\n")
    if _supports_color(stream):
        return f"\033[36m{art}\033[0m"
    return art


def _hr(width: int = 52) -> str:
    return "-" * width


_MENU = """
{banner}
==================================================
   devprogrmer * IP Health Scanner   (v{version})
==================================================
  1) Scan saved ranges (datacenter / CDN / both)
  2) Discover & save provider ranges
  3) Manage IP ranges
  4) View scan history
  5) Settings
  6) Update installed version
  7) Filter CIDRs by first octet
  0) Exit
{rule}"""


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
        first = True
        while True:
            banner = render_banner(self.stdout) if first else ""
            first = False
            self._print(
                _MENU.format(banner=banner, version=__version__, rule=_hr())
            )
            try:
                choice = self._prompt("Select an option: ")
            except EOFError:
                self._print("\nGoodbye.")
                return 0

            try:
                if choice == "1":
                    self._scan_saved()
                elif choice == "2":
                    self._discover_and_save()
                elif choice == "3":
                    self._manage_ranges()
                elif choice == "4":
                    self._history()
                elif choice == "5":
                    self._settings()
                elif choice == "6":
                    self._update_installed_version()
                elif choice == "7":
                    self._filter_by_first_octet()
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
    def _choose(self, title: str, options: list[tuple[str, str]]) -> str | None:
        """Render a titled numbered menu; return the chosen key or None."""
        self._print(f"\n{title}")
        for i, (_key, label) in enumerate(options, start=1):
            self._print(f"  {i}) {label}")
        raw = self._prompt("Choice: ").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= len(options)):
            self._print("Unknown option.")
            return None
        return options[int(raw) - 1][0]

    def _scan_saved(self) -> None:
        """Ask which class/origin to scan, then scan the saved category ranges.

        Implements the "ask what to scan, then load the correct saved CIDRs"
        flow: the user picks an origin (Iran / Foreign / Both) and a class
        (Datacenter / CDN-Cloud / Both); those resolve to storage categories,
        and the CIDRs previously saved under them (via discovery or manual add)
        are loaded from Manage IP Ranges and scanned. Nothing is re-entered by
        hand and nothing is re-discovered here.
        """
        origin = self._choose(
            "Which origin?",
            [("iran", "Iran"), ("foreign", "Foreign"), ("both", "Both")],
        )
        if origin is None:
            return
        cls = self._choose(
            "Which CIDR class?",
            [
                ("datacenter", "Datacenter CIDRs"),
                ("cdn", "CDN / Cloud CIDRs"),
                ("both", "Both"),
            ],
        )
        if cls is None:
            return

        categories = _categories_for(origin, cls)
        # Load saved CIDRs for the selected categories, remembering each CIDR's
        # metadata so results can be grouped by country later.
        entries: list[ranges_mod.RangeEntry] = []
        for category in categories:
            entries.extend(ranges_mod.category_entries(category))
        if not entries:
            self._print(
                "\nNo saved CIDRs for that selection yet. Run "
                "'Discover & save provider ranges' first."
            )
            return

        hosts = ranges_mod.expand_hosts(
            [e.cidr for e in entries],
            sample_per_range=self.settings.sample_per_range,
            max_hosts=self.settings.max_hosts,
        )
        if not hosts:
            self._print("Selected ranges expanded to no scannable hosts.")
            return



        cat_label = ", ".join(categories)
        self._print(
            f"\nScanning {len(hosts)} host(s) from {len(entries)} saved "
            f"CIDR(s) [{cat_label}] ({self.settings.ping_count} probe(s) each)...\n"
        )
        # Map probe host -> the source CIDR's metadata for latency grouping.
        host_to_record = self._map_hosts_to_entries(hosts, entries)

        bar = ProgressBar(len(hosts), stream=self.stdout, label="Scan")

        def _hook(_probe, verdict: str) -> None:
            bar.update(verdict)

        scope = categories[0] if len(categories) == 1 else origin
        report = scanner.run_scan(scope, self.settings, on_result=_hook, hosts=hosts)
        bar.finish()

        scan_id = scanner.persist(report, self.store)
        self._render_scan_results(report, host_to_record, scan_id, origin=origin)

    def _map_hosts_to_entries(
        self, hosts: list[str], entries: list[ranges_mod.RangeEntry]
    ) -> dict[str, object]:
        """Map each probe host back to the entry (metadata) of its CIDR."""
        import ipaddress as _ip

        nets = []
        for e in entries:
            try:
                nets.append((_ip.ip_network(e.cidr, strict=False), e))
            except ValueError:
                continue
        mapping: dict[str, object] = {}
        for host in hosts:
            try:
                addr = _ip.ip_address(host)
            except ValueError:
                continue
            for net, entry in nets:
                if addr in net:
                    mapping[host] = entry
                    break
        return mapping

    def _render_scan_results(
        self, report, host_to_record: dict[str, object], scan_id: int, *, origin: str
    ) -> None:
        """Print the summary, lowest-latency grouping, and clean bare-IP block."""
        self._print("")
        self._print(
            report_mod.summary_line(report.counts, report.total, stream=self.stdout)
        )

        live = [(p, v) for p, v in report.results if p.reachable]
        if live:
            groups = scanner.summarize_by_group(report.results, host_to_record)
            self._print("\n" + _hr())
            self._print("Latency by destination country (from this server):")
            self._print(report_mod.render_group_latency(groups))

        self._print(_hr())
        self._print("Alive IPs (copy-paste ready):")
        if live:
            self._print(format_bare_ips(p.host for p, _v in live))
            self._print("\nWith metadata:")
            for probe, _v in live:
                rec = host_to_record.get(probe.host)
                country = getattr(rec, "country", None) or "?"
                provider = getattr(rec, "provider", None) or "?"
                latency = f"{probe.avg_ms:.0f}ms" if probe.avg_ms is not None else "?"
                self._print(f"  {probe.host}  [{country}]  {provider}  {latency}")
        else:
            self._print("(no live IPs found)")
        self._print(
            f"\nSaved as scan #{scan_id}. Use 'View scan history' to revisit it."
        )

    def _discover_and_save(self) -> None:
        """Discover CIDRs across many providers and auto-save them by category.

        Aggregates the bundled provider seed data (broad, deterministic coverage
        across datacenter/hosting/cloud/CDN for Iran and foreign) together with
        live/offline pipeline discovery, classifies every record, and persists
        the CIDRs into Manage IP Ranges. This is what fixes the old one-shot
        "Iranian CDN" flow that returned a single provider: it now saves ALL
        matching providers' CIDRs, durably.
        """
        from .. import pipeline
        from ..config import load_config
        from . import providers as providers_mod

        self._print("\nDiscovering provider ranges (seed data + sources)...")
        records = list(providers_mod.load_seed_records())
        try:
            config = load_config()
            raw = pipeline.discover(config)
            records.extend(pipeline.process(raw, config.to_filters()))
        except Exception as exc:  # noqa: BLE001 - discovery must never crash the menu
            self._print(f"  (live discovery unavailable: {exc})")

        added = ranges_mod.persist_records(records)
        total = sum(added.values())
        self._print(f"\n{_hr()}")
        if not total:
            self._print(
                "No new CIDRs to save — everything discovered is already stored."
            )
        else:
            self._print(f"Saved {total} new CIDR(s) into Manage IP Ranges:")
            for category in ranges_mod.CATEGORIES:
                if category in added:
                    self._print(f"  {category:20s} +{added[category]}")
        # Show the running totals per category so the user sees persistence.
        self._print("\nStored totals by category:")
        for category in ranges_mod.CATEGORIES:
            self._print(
                f"  {category:20s} {len(ranges_mod.load_category(category))}"
            )
        self._print(
            "\nThese persist across restarts — scan them any time from "
            "'Scan saved ranges'."
        )

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
                "  1) List ranges by category\n"
                "  2) List Iranian ranges (all)\n"
                "  3) List foreign ranges (all)\n"
                "  4) Add a custom range\n"
                "  5) Remove a custom range\n"
                "  0) Back"
            )
            choice = self._prompt("Select: ")
            if choice == "1":
                self._list_categories()
            elif choice == "2":
                self._list_ranges("iran")
            elif choice == "3":
                self._list_ranges("foreign")
            elif choice == "4":
                self._add_range()
            elif choice == "5":
                self._remove_range()
            elif choice in ("0", ""):
                return
            else:
                self._print("Unknown option.")

    def _list_categories(self) -> None:
        """Show saved CIDRs grouped by the four categories, with origin tags."""
        for category in ranges_mod.CATEGORIES:
            entries = ranges_mod.category_entries(category)
            self._print(f"\n{category} ({len(entries)}):")
            if not entries:
                self._print("  (none)")
                continue
            for e in entries:
                origin_tag = "[discovered]" if e.origin == "discovered" else "[custom]"
                meta = f" {e.country or '?'} / {e.provider or '?'}"
                self._print(f"  {e.cidr:<20} {origin_tag}{meta}")

    def _list_ranges(self, scope: str) -> None:
        all_ranges = ranges_mod.load_ranges(scope)
        custom = set(ranges_mod.custom_ranges(scope))
        # A scope also aggregates its two categories.
        for category in ranges_mod._SCOPE_CATEGORIES[scope]:
            custom |= set(ranges_mod.custom_ranges(category))
        self._print(f"\n{scope.title()} ranges ({len(all_ranges)} total):")
        for cidr in all_ranges:
            tag = " [saved]" if cidr in custom else ""
            self._print(f"  {cidr}{tag}")

    def _group_prompt(self) -> str | None:
        """Prompt for a scope or category name for add/remove."""
        groups = (*ranges_mod.SCOPES, *ranges_mod.CATEGORIES)
        self._print("\nGroups: " + ", ".join(groups))
        group = self._prompt("Group: ").strip().lower()
        if group not in groups:
            self._print(f"Group must be one of: {', '.join(groups)}.")
            return None
        return group

    def _add_range(self) -> None:
        group = self._group_prompt()
        if group is None:
            return
        cidr = self._prompt("CIDR to add (e.g. 185.51.200.0/22): ")
        try:
            normalized = ranges_mod.add_custom_range(group, cidr)
        except ValueError as exc:
            self._print(f"Could not add range: {exc}")
            return
        self._print(f"Added {normalized} to {group} ranges.")

    def _remove_range(self) -> None:
        group = self._group_prompt()
        if group is None:
            return
        custom = ranges_mod.custom_ranges(group)
        if not custom:
            self._print(f"No custom/discovered {group} ranges to remove.")
            return
        self._print(f"Saved {group} ranges:")
        for cidr in custom:
            self._print(f"  {cidr}")
        cidr = self._prompt("CIDR to remove: ")
        if ranges_mod.remove_custom_range(group, cidr):
            self._print(f"Removed {cidr}.")
        else:
            self._print("That range was not found among saved ranges.")

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
