# Contributing to gaming

Thanks for your interest in improving **gaming** — a network discovery and
reachability analysis CLI (not a video game). This guide covers local setup,
the development workflow, and how to extend the tool.

## Development setup

Requires Python **3.11+**.

```bash
git clone https://github.com/your-org/gaming
cd gaming
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Everyday commands

```bash
make test        # run the test suite
make lint        # ruff check
make format      # ruff format + import sort
make cov         # tests with coverage report
make build       # build sdist + wheel and verify with twine
make check       # lint + tests (what CI gates on)
```

Without `make`:

```bash
ruff check .
pytest --cov=gaming
python -m build && twine check dist/*
```

## Guidelines

- **Tests are required.** Every change should keep the suite green and add
  coverage for new behavior. Tests must run fully offline — no live network
  calls (use `monkeypatch` and the sources' sample data).
- **Style** is enforced by `ruff` (config in `pyproject.toml`). Run `make format`
  before committing.
- **Keep it dependency-free.** The runtime uses only the Python standard library.
  Please do not add third-party runtime dependencies without discussion.
- **Commit messages**: short imperative subject; explain the "why" in the body.

## Adding a discovery source

1. Create `src/gaming/discovery/<name>.py` with a class subclassing `Source`:

   ```python
   from .base import Source
   from ..models import IPRecord

   class MySource(Source):
       name = "mysource"

       def _discover_online(self):
           ...  # perform the real lookup; may raise (errors are handled)
           return []  # list[IPRecord]

       def _sample_data(self):
           return [IPRecord(prefix="203.0.113.0/24", source=self.name, country="US")]
   ```

2. Register it in `src/gaming/discovery/__init__.py` under `REGISTRY`.
3. Add tests covering both the online path (mocked) and sample fallback.

## Adding an output format

Add a module under `src/gaming/reporting/`, expose it from that package's
`__init__.py`, and wire it into `export()` and the CLI `--format` choices.

## Responsible use

This project performs network reconnaissance. Only contribute features intended
for use against infrastructure the operator is authorized to assess. See the
[Responsible use](README.md#responsible-use) section of the README.
