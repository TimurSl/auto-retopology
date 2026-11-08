# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences."""

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences

from . import ui


def _update_category(self, context):
    ui.update_category(self.panel_category)


class AutoRetopoPreferences(AddonPreferences):
    bl_idname = __package__

    panel_category: StringProperty(
        name="Sidebar Tab", default=ui.DEFAULT_CATEGORY, update=_update_category,
        description="Name of the 3D Viewport sidebar tab that holds the panels",
    )
    max_voxel_resolution: IntProperty(
        name="Max Voxel Resolution", default=1024, min=64, max=8192,
        description="Safety limit: voxel passes never use more voxels than this along the "
                    "longest side. Raise it for huge detailed meshes (uses more memory)",
    )
    show_tips: BoolProperty(name="Show Tips", default=True,
                            description="Show hints inside the panels")
    verbose: BoolProperty(name="Log Progress", default=False,
                          description="Print every pipeline step to the system console")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "panel_category")
        layout.prop(self, "max_voxel_resolution")
        layout.prop(self, "show_tips")
        layout.prop(self, "verbose")


def get_category(context):
    addon = context.preferences.addons.get(__package__)
    if addon and addon.preferences:
        return addon.preferences.panel_category
    return ui.DEFAULT_CATEGORY


def register():
    bpy.utils.register_class(AutoRetopoPreferences)


def unregister():
    bpy.utils.unregister_class(AutoRetopoPreferences)
