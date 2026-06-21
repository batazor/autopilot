"""Dreamscape Memory solver internals.

Cohesive pieces extracted from the module's ``exec.py`` (which is loaded by
``config.module_exec_registry`` via ``spec_from_file_location``, so these
submodules are imported by their absolute namespace-package path). ``exec.py``
re-exports the moved names so its handlers — and the unit tests that load it by
path — keep referring to them unchanged.
"""
