"""Full kingdom map scan via the minimap.

The map traversal is a constant list of minimap taps computed from
``radar_config.yaml``. At runtime there is no CV for navigation: tap → wait
for stabilization → screenshot → next point (``radar scan``). Frames plus a
``manifest.json`` land in the run directory; ``radar stitch`` assembles them
into one canvas.

Modules:
    config      -- pydantic config model + YAML load/save (radar_config.yaml)
    geometry    -- pure functions: diamond, grid, affine transform
    device      -- standalone capture/tap wrapper (no Redis, no worker)
    scanner     -- main scan loop (tap grid, stabilization, manifest)
    stitch      -- frame stitching into a canvas
    cli         -- entry points: scan / stitch
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
