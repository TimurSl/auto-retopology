# Auto Retopology for Blender

One-click, highly configurable automatic retopology. Pick a method and a density, press
**Retopologize**, and get a clean quad mesh projected back onto your original. 90
settings let you tune each stage, from input cleanup to multires detail baking.

![Before / after: noisy sculpt, beveled hard-surface box, symmetric torus](docs/before_after.png)

*Left of each pair: original. Right: Auto Retopology result (QuadriFlow, default settings
+ target face count; the box uses **Preserve Sharp**, the torus uses **Symmetry X**).*

## Features

- **4 methods**: QuadriFlow quad remesh, Voxel remesh, Decimate (Collapse / Un-Subdivide /
  Planar) and Octree remesh (Blocks / Smooth / Sharp).
- **Unified density control**: target face count, ratio of the input, or edge length in world
  units, applied the same way by every method.
- **Robust input handling**: a voxel cleanup pass makes any mesh manifold for QuadriFlow, heavy
  meshes are pre-decimated, size is normalized so tiny real-world objects work, and failed runs
  retry with a new seed.
- **Pre-processing**: merge by distance, delete loose geometry, remove small floating parts,
  fill holes, recalculate normals, pre-smooth noisy scans, symmetrize the input.
- **True symmetry**: cut, weld the seam and mirror on X/Y/Z, keeping the + or − side.
  The Mirror modifier can be applied or kept live.
- **Relax & projection**: tangential relax that keeps volume and sticks to the surface.
  Shrinkwrap offers 4 wrap methods and 5 snap modes. **Hybrid projection** never snaps
  onto hidden inner geometry such as overlapping parts or eyes in sockets.
- **Topology & shading**: triangles→quads, degenerate cleanup, welding, optional final
  triangulation, flat / smooth / auto-smooth by angle.
- **UVs & data**: Smart UV Project, Lightmap Pack, or transfer UVs from the original.
  Color attributes, vertex groups and custom normals can be transferred too.
- **Subdivision & detail**: a Subdivision Surface preview, or a **Multires** modifier with the
  original detail projected in, ready for sculpting or normal-map baking.
- **Workflow**: batch-process all selected objects. Select a result to re-run it with new
  settings (the old result is replaced). There is a "replace mesh" output mode, an output
  collection, and display options for the original and the result.
- **Presets**: 9 built-in quick presets (Game Low/Mid/LOD, Organic, Sculpt Base, Hard
  Surface, 3D Scan, Fast Preview, Voxel Art). You can also save your own user presets.
- **Tools**: mesh analysis (quads/tris/n-gons, poles, islands, non-manifold edges, average
  edge length), toggle between original and result, apply the live retopo modifiers, and a
  **Manual Retopo Setup** that adds snapping, shrinkwrap and mirror for hand retopology.

## Installation

Requires **Blender 4.2 or newer**. It was developed and tested against Blender 5.1.

1. Download the latest `auto_retopology-x.y.z.zip` from the releases, or build it yourself with
   `python build.py`, which writes `dist/auto_retopology-<version>.zip`.
2. In Blender go to **Edit › Preferences › Get Extensions**, open the **⌄** menu, choose
   **Install from Disk…** and select the zip. On older Blender versions use
   **Preferences › Add-ons › Install**.
3. Open the sidebar in the 3D Viewport (**N**) and find the **Retopo** tab.

The add-on is also available in **Object › Auto Retopology** and in the object right-click
menu.

## Quick start

1. Select your mesh(es).
2. Optionally choose a **Quick Preset**.
3. Choose a **Method** and a density, for example *Face Count = 5000*.
4. Click **Retopologize**.

The result is called `<name>_retopo`. The original is hidden by default; use
**Tools › Toggle Original / Result** to compare. To try different settings, keep the result
selected and press **Retopologize** again. It re-runs from the original and replaces the
previous result.

## Settings reference

### Main
| Setting | Description |
|---|---|
| Method | QuadriFlow, Voxel, Decimate or Octree |
| Density | **Face Count**, **Ratio** of input faces, or **Edge Length** (world units) |
| Process All Selected | Batch all selected meshes, or only the active one |

### Method settings
| Method | Settings |
|---|---|
| QuadriFlow | Preserve Sharp, Preserve Boundary, Preserve Attributes, Smooth Normals, Native Symmetry, Seed, Retries |
| Voxel | Adaptivity, Fix Poles, Preserve Volume, Preserve Attributes |
| Decimate | Type (Collapse / Un-Subdivide / Planar), Triangulate, Convert to Quads, Iterations, Angle Limit, Delimit |
| Octree | Mode (Blocks / Smooth / Sharp), Depth from Density, Octree Depth, Scale, Sharpness, Remove Disconnected + Threshold |

### Pre-processing
Apply Modifiers · Use World Scale · Merge by Distance (+ distance) · Delete Loose ·
Remove Small Parts (+ min faces) · Fill Holes (+ max sides) · Recalculate Normals ·
Pre-Smooth iterations / factor · **Voxel Cleanup** (+ detail) · **Pre-Decimate** (+ max faces)

### Symmetry
Enable · Axis (X/Y/Z) · Keep side (+/−) · Symmetrize Input · Merge Distance · Apply Mirror.
The mirror plane passes through the object origin.

### Relax & Projection
Relax Iterations / Strength · Lock Boundary · Stick to Surface ·
**Project Onto** (Hybrid / Outer Shell / Original Surface) · Shrinkwrap (Wrap Method,
Snap Mode, Offset, Apply)

### Topology & Shading
Triangles to Quads (+ face / shape angle) · Dissolve Degenerate · Weld Distance ·
Recalculate Normals · Triangulate Result (+ quad method) · Shading (Keep / Flat / Smooth /
Auto Smooth + angle)

### UVs & Data Transfer
UVs (None / Transfer / Smart UV Project / Lightmap Pack + angle limit, island margin) ·
Transfer Color Attributes / Vertex Groups / Custom Normals · Apply Transfer · Copy Materials

### Subdivision & Detail
None / Subdivision Surface (viewport + render levels) / **Multires (Bake Detail)** (levels)

### Output
New Object / Replace Mesh · Suffix · Collection · Replace Previous Result ·
Original display (Keep / Hide / Wire / Bounds) · Show Wireframe · In Front

### Preferences
Sidebar tab name · Max voxel resolution (memory safety limit) · Show tips · Log progress.

## Tips

- **QuadriFlow fails?** Keep **Voxel Cleanup** enabled. It makes any input watertight. Increase
  *Cleanup Detail* to keep finer features.
- **Thin parts disappear** (ears, fingers, cloth)? Increase the target face count or use a
  smaller edge length. Voxel-based methods can't represent parts thinner than about two voxels.
- **Non-watertight inputs** (open holes) can confuse every voxel-based method. Enable
  **Fill Holes** first.
- **Game assets**: use *Triangulate Result*, *Smart UV Project* and *Auto Smooth*, or the
  *Game Asset* presets.
- **Keep editing one half**: enable *Symmetry* and turn off *Apply Mirror*.
- **Sculpting**: use the *Sculpt Base (Multires)* preset to get a clean base with the
  original detail baked into the multires levels.

## Scripting

The pipeline has no UI dependencies:

```python
import bpy
from auto_retopology import core          # bl_ext.user_default.auto_retopology when installed as an extension

settings = bpy.context.scene.auto_retopo
settings.method = 'QUADRIFLOW'
settings.target_faces = 3000
info = core.run_retopology(bpy.context, bpy.context.active_object, settings)
print(info["object"].name, info["output_faces"], info["warnings"])
```

## Development

Run the headless test suite (25 tests covering every method, option path and UI panel):

```bash
blender --background --factory-startup --python tests/test_addon.py
# or, with the PyPI `bpy` module (Python 3.13):
pip install bpy && python tests/test_addon.py
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
