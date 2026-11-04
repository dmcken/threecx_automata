# threecx_automata
[![Tests](https://github.com/dmcken/threecx_automata/actions/workflows/tests.yml/badge.svg)](https://github.com/dmcken/threecx_automata/actions/workflows/tests.yml)
[![Ruff](https://github.com/dmcken/threecx_automata/actions/workflows/ruff.yml/badge.svg)](https://github.com/dmcken/threecx_automata/actions/workflows/ruff.yml)

A 3CX Web GUI automation library.

## Install

```bash
pip install -U git+https://github.com/dmcken/threecx_automata.git
or
python3 -m pip install -U git+https://github.com/dmcken/threecx_automata.git
```

This tracks the default branch, which moves as new commits land - fine
for local testing, but Docker builds and other downstream consumers
should pin to a released tag instead so builds don't silently change
underneath them:

```bash
pip install git+https://github.com/dmcken/threecx_automata.git@v0.3.0
```

Available tags: https://github.com/dmcken/threecx_automata/tags

## Testing

```bash
pytest
```

`tests/test_live.py` additionally runs read-only checks against a real
server when a `.env` file provides `DOMAIN`, `USERNAME` and `PASSWORD`;
otherwise those tests are skipped.
