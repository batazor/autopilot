"""Wiki sync helpers.

Empty marker so ``src/wiki`` is a regular package, which lets the Nuitka
discovery in ``scripts/compile_protected.sh`` pick it up and compile
``sync_runner.py`` into the resulting ``wiki.so``. Without this file, ``wiki``
would be a namespace package and the script's ``find -name __init__.py`` walk
would skip it, leaving ``sync_runner.py`` readable in the runtime image.
"""
