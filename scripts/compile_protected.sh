#!/usr/bin/env bash
# Compile selected Python packages to native ``.so`` modules via Nuitka.
#
# Each package in ``WOS_NUITKA_PACKAGES`` is compiled as a *whole package*:
#   nuitka --module licensing/   →  licensing.cpython-<ver>-<arch>.so
# Then the source directory is deleted. Python's import system picks up the
# ``.so`` (it's a single-file extension module that registers itself as a
# package with all submodules accessible via ``import licensing.verify`` etc).
#
# Why not compile per-file? Nuitka refuses to compile ``__init__.py`` in
# isolation (it needs the full package context to resolve relative imports),
# and per-file compilation would also leave the rest of the package readable.
# The whole-package mode produces a single opaque ``.so`` per package, which
# is both stronger protection and easier to reason about.
#
# Inputs (env vars):
#   WOS_NUITKA_PACKAGES   space-separated package names under ``SRC_ROOT``
#   SRC_ROOT              path where packages live (default: ``/app/src``)
#   PYTHON                Python interpreter that owns the target venv
#
# Exit codes: non-zero on any compile failure (fail the Docker build loudly).

set -euo pipefail

: "${WOS_NUITKA_PACKAGES:?WOS_NUITKA_PACKAGES is required (space-separated package list, or '__all__')}"
: "${SRC_ROOT:=/app/src}"
: "${PYTHON:=/app/.venv/bin/python}"

# Sanity: Nuitka is only an *optional* dep, so give a clear error if the
# caller forgot ``--extra compile``.
if ! "${PYTHON}" -c "import nuitka" >/dev/null 2>&1; then
    echo "error: nuitka is not installed in ${PYTHON}" >&2
    echo "       run \`uv sync --frozen --extra compile\` before invoking this script" >&2
    exit 1
fi

cd "${SRC_ROOT}"

# Resolve ``__all__`` to every top-level package under SRC_ROOT (any directory
# with an ``__init__.py``). Lets the Dockerfile default to "compile everything"
# without enumerating package names in two places.
if [ "${WOS_NUITKA_PACKAGES}" = "__all__" ]; then
    PKG_LIST=$(find . -mindepth 2 -maxdepth 2 -name '__init__.py' -type f \
        | sed -e 's|^\./||' -e 's|/__init__\.py$||' \
        | sort)
    if [ -z "${PKG_LIST}" ]; then
        echo "error: __all__ found no packages under ${SRC_ROOT}" >&2
        exit 1
    fi
    echo "==> auto-discovered $(echo "${PKG_LIST}" | wc -l | tr -d ' ') packages"
else
    PKG_LIST="${WOS_NUITKA_PACKAGES}"
fi

for pkg in ${PKG_LIST}; do
    if [ ! -d "${pkg}" ]; then
        echo "error: package directory '${SRC_ROOT}/${pkg}' does not exist" >&2
        exit 1
    fi

    echo "==> compiling package: ${pkg}"

    # ``--module <pkg>/`` produces ``<pkg>.cpython-<ver>-<arch>.so`` next to
    # the source dir. The single .so contains the whole package with all
    # submodules — ``import licensing.verify`` still works.
    #
    # ``--include-package=<pkg>`` is needed when the package is *not yet*
    # importable from the current cwd; here we're under ``SRC_ROOT`` and
    # the editable install in ``.venv`` points here, so the package is
    # already on sys.path — but we pass it explicitly to be defensive.
    "${PYTHON}" -m nuitka \
        --module \
        --include-package="${pkg}" \
        --remove-output \
        --python-flag=no_docstrings \
        --quiet \
        --no-progressbar \
        --output-dir=. \
        "${pkg}"

    # Strip Python source from the package dir while *keeping* any non-Python
    # data files (e.g. ``config/settings.yaml``, ``licensing/public_key.pem``,
    # ``config/balance/*.yaml``, ``navigation/*.yaml``). Nuitka preserves the
    # original ``__file__`` paths inside the compiled module, so code that does
    # ``Path(__file__).parent / "data.yaml"`` still resolves correctly as long
    # as the data file lives at its original on-disk location.
    find "${pkg}" -type f \( -name '*.py' -o -name '*.pyc' \) -delete
    find "${pkg}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    # If the package had no data files at all, the directory is now empty —
    # remove it so Python doesn't see a stale namespace package alongside the
    # ``.so`` (extension modules take precedence over namespace packages, but
    # leaving an empty dir is just noise).
    find "${pkg}" -type d -empty -delete 2>/dev/null || true

    # The produced ``.so`` should now sit at ``<pkg>.cpython-<ver>-<arch>.so``.
    if ! ls "${pkg}".cpython-*.so >/dev/null 2>&1; then
        echo "error: expected ${SRC_ROOT}/${pkg}.cpython-*.so to exist after compilation" >&2
        exit 1
    fi

    # Drop the type stub Nuitka emits alongside the .so. It's only useful for
    # IDE autocomplete / mypy and *leaks* every public function name, parameter
    # name, type signature, and docstring — exactly the metadata a reverse
    # engineer would need to locate symbols inside the compiled binary. Python
    # never reads .pyi at runtime, so removing them is a pure win for obfuscation.
    rm -f "${pkg}.pyi"
done

# Global cleanup of ``__pycache__`` everywhere under SRC_ROOT. Per-package
# cleanup only ran for packages that got compiled — leftover directories
# (e.g. ``src/ui/__pycache__`` whose ``.py`` source isn't even shipped) would
# otherwise carry readable bytecode that defeats the whole point of compiling.
find "${SRC_ROOT}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Then drop any now-empty top-level directories (e.g. ``src/ui/`` which had
# nothing but ``__pycache__``). Dirs that still hold data files survive.
find "${SRC_ROOT}" -mindepth 1 -maxdepth 1 -type d -empty -delete 2>/dev/null || true

echo "==> done."
