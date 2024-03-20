"""Stub to handle cases when Atheris is not installed.

Atheris is only needed during fuzzing with OSS-Fuzz. It does not need to be
installed while building/testing pyWeMo. This provides stub methods for when
Atheris is not installed.
"""

try:
    from atheris import *  # noqa
except ImportError:
    import contextlib

    @contextlib.contextmanager
    def instrument_imports():
        yield
