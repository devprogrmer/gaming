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

import sys
from typing import TextIO

from .. import __version__
from . import ranges as ranges_mod
from . import report as report_mod
from . import scanner
from .progress import ProgressBar
from .settings import Settings, load_settings, save_settings
from .storage import HistoryStore

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


def run(argv: list[str] | None = None) -> int:
    """Entry point for the interactive menu."""
    return Menu().run()
