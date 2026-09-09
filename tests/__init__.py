"""Test package.

``tests`` is a real package so that ``from tests.conftest import ...`` resolves
the same way under ``pytest`` and ``python -m pytest``. Without it the import
only works when the current working directory happens to be the repository
root, which is exactly the difference between a green local run and a red CI
run.
"""
