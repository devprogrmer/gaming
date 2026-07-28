"""gaming — Network discovery and reachability analysis CLI tool.

Despite the project name, this package has nothing to do with video games.
It discovers IP ranges from public network data sources (RDAP, WHOIS,
ASN/BGP, PeeringDB, RIR allocations), filters and normalizes them, checks
reachability, optionally probes ports and performs global reachability
checks, and exports structured reports.
"""

__version__ = "0.9.0"

__all__ = ["__version__"]
