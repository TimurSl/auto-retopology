# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto Retopology - one-click, highly configurable automatic retopology for Blender.

Sidebar: 3D Viewport > N > Retopo
"""

bl_info = {
    "name": "Auto Retopology",
    "author": "TimurSl",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Retopo",
    "description": "Automatic quad retopology with QuadriFlow, Voxel, Decimate and Octree "
                   "methods plus symmetry, relax, projection, UV and detail baking options",
    "category": "Mesh",
    "doc_url": "https://github.com/TimurSl/auto-retopology",
    "tracker_url": "https://github.com/TimurSl/auto-retopology/issues",
}

if "bpy" in locals():
    import importlib
    for _mod in (properties, core, presets, operators, ui, preferences):  # noqa: F821
        importlib.reload(_mod)
else:
    from . import properties, core, presets, operators, ui, preferences

import bpy  # noqa: E402


def register():
    properties.register()
    presets.register()
    operators.register()
    preferences.register()
    ui.register(preferences.get_category(bpy.context))


def unregister():
    ui.unregister()
    preferences.unregister()
    operators.unregister()
    presets.unregister()
    properties.unregister()
