# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in quick presets and user preset support."""

import math

import bpy
from bl_operators.presets import AddPresetBase
from bpy.types import Menu, Operator

from .properties import AutoRetopoSettings

PRESET_SUBDIR = "auto_retopology"

# Every setting not listed in a preset falls back to its default.
QUICK_PRESETS = {
    'GAME_LOW': dict(
        label="Game Asset - Low Poly",
        description="~1.5k quads, auto smooth shading and a fresh UV unwrap",
        values=dict(method='QUADRIFLOW', target_faces=1500, relax_iterations=2,
                    shading='AUTO', uv_mode='SMART'),
    ),
    'GAME_MID': dict(
        label="Game Asset - Mid Poly",
        description="~6k quads with sharp features preserved and UVs",
        values=dict(method='QUADRIFLOW', target_faces=6000, qf_preserve_sharp=True,
                    relax_iterations=3, shading='AUTO', uv_mode='SMART'),
    ),
    'GAME_LOD': dict(
        label="Game LOD (Keep UVs)",
        description="Decimate to 25%, triangulated, keeps original UVs and colors",
        values=dict(method='DECIMATE', density_mode='RATIO', target_ratio=0.25,
                    dc_tris_to_quads=False, dc_triangulate=True, post_tris_to_quads=False,
                    relax_iterations=0, use_shrinkwrap=False, post_triangulate=True,
                    uv_mode='TRANSFER', transfer_colors=True, shading='AUTO',
                    pre_merge=False, pre_delete_loose=False),
    ),
    'ORGANIC': dict(
        label="Organic / Character",
        description="~8k quads, X symmetry, smooth shading, subdivision preview",
        values=dict(method='QUADRIFLOW', target_faces=8000, use_symmetry=True,
                    relax_iterations=4, shading='SMOOTH', subdiv_mode='SUBSURF',
                    subdiv_levels=1, subdiv_render_levels=2),
    ),
    'SCULPT_BASE': dict(
        label="Sculpt Base (Multires)",
        description="~3k quads with the original detail baked into a 2-level Multires",
        values=dict(method='QUADRIFLOW', target_faces=3000, relax_iterations=5,
                    shading='SMOOTH', subdiv_mode='MULTIRES', subdiv_levels=2),
    ),
    'HARD_SURFACE': dict(
        label="Hard Surface",
        description="Preserves sharp edges and boundaries, flat-ish auto smooth at 30°",
        values=dict(method='QUADRIFLOW', target_faces=4000, qf_preserve_sharp=True,
                    qf_preserve_boundary=True, pre_voxel_detail=6.0, relax_iterations=0,
                    shading='AUTO', auto_smooth_angle=math.radians(30.0)),
    ),
    'SCAN': dict(
        label="3D Scan / Photogrammetry",
        description="Cleans noisy, holey scans: fill holes, drop debris, smooth, then quad remesh",
        values=dict(method='QUADRIFLOW', target_faces=10000, pre_fill_holes=True,
                    pre_remove_small_parts=True, pre_small_part_faces=200,
                    pre_smooth_iterations=2, pre_merge_distance=0.001,
                    relax_iterations=3, shading='SMOOTH', uv_mode='SMART',
                    transfer_colors=True),
    ),
    'PREVIEW': dict(
        label="Fast Preview",
        description="Quick voxel remesh for blocking out - no relax, no extras",
        values=dict(method='VOXEL', density_mode='FACES', target_faces=5000,
                    relax_iterations=0, use_shrinkwrap=False, shading='SMOOTH'),
    ),
    'BLOCKS': dict(
        label="Voxel Art (Blocks)",
        description="Blocky Octree remesh, flat shaded",
        values=dict(method='OCTREE', oc_mode='BLOCKS', oc_auto_depth=False, oc_depth=5,
                    oc_remove_disconnected=False, relax_iterations=0, use_shrinkwrap=False,
                    post_tris_to_quads=False, shading='FLAT'),
    ),
}


def setting_names():
    return [name for name in AutoRetopoSettings.__annotations__.keys()]


def reset_settings(settings):
    for name in setting_names():
        settings.property_unset(name)


def apply_quick_preset(settings, key):
    reset_settings(settings)
    for name, value in QUICK_PRESETS[key]['values'].items():
        setattr(settings, name, value)


class AUTORETOPO_OT_quick_preset(Operator):
    bl_idname = "auto_retopo.quick_preset"
    bl_label = "Apply Quick Preset"
    bl_description = "Reset all settings and apply a built-in preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(
        name="Preset",
        items=[(k, v['label'], v['description']) for k, v in QUICK_PRESETS.items()],
    )

    def execute(self, context):
        apply_quick_preset(context.scene.auto_retopo, self.preset)
        self.report({'INFO'}, f"Applied preset: {QUICK_PRESETS[self.preset]['label']}")
        return {'FINISHED'}


class AUTORETOPO_OT_reset_settings(Operator):
    bl_idname = "auto_retopo.reset_settings"
    bl_label = "Reset Settings"
    bl_description = "Reset every Auto Retopology setting to its default value"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        reset_settings(context.scene.auto_retopo)
        return {'FINISHED'}


class AUTORETOPO_MT_quick_presets(Menu):
    bl_idname = "AUTORETOPO_MT_quick_presets"
    bl_label = "Quick Presets"

    def draw(self, context):
        layout = self.layout
        for key, data in QUICK_PRESETS.items():
            layout.operator(AUTORETOPO_OT_quick_preset.bl_idname,
                            text=data['label']).preset = key


class AUTORETOPO_MT_user_presets(Menu):
    bl_idname = "AUTORETOPO_MT_user_presets"
    bl_label = "User Presets"
    preset_subdir = PRESET_SUBDIR
    preset_operator = "script.execute_preset"
    draw = Menu.draw_preset


class AUTORETOPO_OT_add_user_preset(AddPresetBase, Operator):
    bl_idname = "auto_retopo.add_user_preset"
    bl_label = "Add Auto Retopology Preset"
    bl_description = "Save the current settings as a preset (or remove one)"
    preset_menu = "AUTORETOPO_MT_user_presets"
    preset_subdir = PRESET_SUBDIR
    preset_defines = ["settings = bpy.context.scene.auto_retopo"]

    @property
    def preset_values(self):
        return [f"settings.{name}" for name in setting_names()]


classes = (
    AUTORETOPO_OT_quick_preset,
    AUTORETOPO_OT_reset_settings,
    AUTORETOPO_MT_quick_presets,
    AUTORETOPO_MT_user_presets,
    AUTORETOPO_OT_add_user_preset,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
