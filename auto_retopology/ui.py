# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels (3D Viewport > N panel > Retopo) and menu entries."""

import bpy
from bpy.types import Menu, Panel

from .presets import AUTORETOPO_MT_quick_presets, AUTORETOPO_MT_user_presets

DEFAULT_CATEGORY = "Retopo"


def _prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


class AutoRetopoPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = DEFAULT_CATEGORY

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH', 'SCULPT'}


class AutoRetopoSubPanel(AutoRetopoPanel):
    bl_parent_id = "AUTORETOPO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}


def _tip(layout, context, text, icon='INFO'):
    prefs = _prefs(context)
    if prefs is None or prefs.show_tips:
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text=text, icon=icon)


# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_main(AutoRetopoPanel, Panel):
    bl_idname = "AUTORETOPO_PT_main"
    bl_label = "Auto Retopology"

    def draw_header_preset(self, context):
        row = self.layout.row(align=True)
        row.menu(AUTORETOPO_MT_user_presets.bl_idname, text="", icon='PRESET')
        row.operator("auto_retopo.add_user_preset", text="", icon='ADD')
        row.operator("auto_retopo.add_user_preset", text="", icon='REMOVE').remove_active = True

    def draw(self, context):
        layout = self.layout
        s = context.scene.auto_retopo
        ob = context.active_object

        row = layout.row(align=True)
        row.menu(AUTORETOPO_MT_quick_presets.bl_idname, icon='SHADERFX')
        row.operator("auto_retopo.reset_settings", text="", icon='LOOP_BACK')

        layout.separator()
        layout.prop(s, "method", text="")

        col = layout.column(align=True)
        col.row(align=True).prop(s, "density_mode", expand=True)
        if s.density_mode == 'FACES':
            col.prop(s, "target_faces")
        elif s.density_mode == 'RATIO':
            col.prop(s, "target_ratio", slider=True)
        else:
            col.prop(s, "target_edge_length")

        if ob is not None and ob.type == 'MESH':
            src = ob.auto_retopo_source
            box = layout.box()
            box.scale_y = 0.8
            if src is not None and s.output_mode == 'NEW_OBJECT':
                box.label(text=f"Result of '{src.name}' - will re-run", icon='FILE_REFRESH')
                faces = len(src.data.polygons)
            else:
                faces = len(ob.data.polygons)
            box.label(text=f"Input: {faces:,} faces (base mesh)", icon='MESH_DATA')
            if s.density_mode == 'FACES':
                box.label(text=f"Target: ~{s.target_faces:,} faces", icon='MOD_REMESH')
            elif s.density_mode == 'RATIO':
                box.label(text=f"Target: ~{max(8, round(faces * s.target_ratio)):,} faces",
                          icon='MOD_REMESH')
            else:
                box.label(text="Target: depends on surface area", icon='MOD_REMESH')

        layout.separator()
        row = layout.row()
        row.scale_y = 1.6
        row.operator("auto_retopo.run", icon='MOD_REMESH', text="Retopologize")
        layout.prop(s, "process_all_selected", text="Process All Selected")


# ---------------------------------------------------------------------------
# Method specific
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_method(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_method"
    bl_label = "Method Settings"
    bl_options = set()

    def draw_header(self, context):
        s = context.scene.auto_retopo
        label = {'QUADRIFLOW': "QuadriFlow", 'VOXEL': "Voxel", 'DECIMATE': "Decimate",
                 'OCTREE': "Octree"}[s.method]
        self.layout.label(text=f"{label} Settings")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        if s.method == 'QUADRIFLOW':
            col = layout.column(heading="Preserve")
            col.prop(s, "qf_preserve_sharp", text="Sharp Edges")
            col.prop(s, "qf_preserve_boundary", text="Boundaries")
            col.prop(s, "qf_preserve_attributes", text="Attributes")
            layout.prop(s, "qf_smooth_normals")
            sub = layout.column()
            sub.active = s.use_symmetry
            sub.prop(s, "qf_native_symmetry")
            col = layout.column(align=True)
            col.prop(s, "qf_seed")
            col.prop(s, "qf_retries")
            _tip(layout, context, "Voxel Cleanup (Pre-processing) avoids most failures")
        elif s.method == 'VOXEL':
            layout.prop(s, "vx_adaptivity", slider=True)
            layout.prop(s, "vx_fix_poles")
            layout.prop(s, "vx_preserve_volume")
            layout.prop(s, "vx_preserve_attributes")
        elif s.method == 'DECIMATE':
            layout.prop(s, "dc_mode")
            if s.dc_mode == 'COLLAPSE':
                layout.prop(s, "dc_triangulate")
                sub = layout.column()
                sub.active = not s.dc_triangulate
                sub.prop(s, "dc_tris_to_quads")
                _tip(layout, context, "Density target is used as the collapse ratio")
            elif s.dc_mode == 'UNSUBDIV':
                layout.prop(s, "dc_iterations")
                _tip(layout, context, "Ignores the density target")
            else:
                layout.prop(s, "dc_angle_limit")
                layout.prop(s, "dc_delimit")
                _tip(layout, context, "Ignores the density target")
        elif s.method == 'OCTREE':
            layout.prop(s, "oc_mode")
            layout.prop(s, "oc_auto_depth")
            sub = layout.column()
            sub.active = not s.oc_auto_depth
            sub.prop(s, "oc_depth")
            layout.prop(s, "oc_scale", slider=True)
            if s.oc_mode == 'SHARP':
                layout.prop(s, "oc_sharpness")
            row = layout.row(heading="Remove Disconnected")
            row.prop(s, "oc_remove_disconnected", text="")
            sub = row.row()
            sub.active = s.oc_remove_disconnected
            sub.prop(s, "oc_threshold", slider=True)


# ---------------------------------------------------------------------------
# Pre-processing
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_preprocess(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_preprocess"
    bl_label = "Pre-processing"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        col = layout.column(heading="Input")
        col.prop(s, "apply_modifiers")
        col.prop(s, "use_world_scale")

        layout.separator()
        row = layout.row(heading="Merge by Distance")
        row.prop(s, "pre_merge", text="")
        sub = row.row()
        sub.active = s.pre_merge
        sub.prop(s, "pre_merge_distance", text="")

        layout.prop(s, "pre_delete_loose")

        row = layout.row(heading="Remove Small Parts")
        row.prop(s, "pre_remove_small_parts", text="")
        sub = row.row()
        sub.active = s.pre_remove_small_parts
        sub.prop(s, "pre_small_part_faces", text="Min Faces")

        row = layout.row(heading="Fill Holes")
        row.prop(s, "pre_fill_holes", text="")
        sub = row.row()
        sub.active = s.pre_fill_holes
        sub.prop(s, "pre_fill_holes_sides", text="Max Sides")

        layout.prop(s, "pre_recalc_normals")

        col = layout.column(align=True)
        col.prop(s, "pre_smooth_iterations")
        sub = col.column(align=True)
        sub.active = s.pre_smooth_iterations > 0
        sub.prop(s, "pre_smooth_factor", slider=True)

        layout.separator()
        col = layout.column()
        col.active = s.method == 'QUADRIFLOW'
        row = col.row(heading="Voxel Cleanup")
        row.prop(s, "pre_voxel_cleanup", text="")
        sub = row.row()
        sub.active = s.pre_voxel_cleanup
        sub.prop(s, "pre_voxel_detail", text="Detail")

        col = layout.column()
        col.active = s.method in {'QUADRIFLOW', 'OCTREE'}
        row = col.row(heading="Pre-Decimate")
        row.prop(s, "pre_decimate", text="")
        sub = row.row()
        sub.active = s.pre_decimate
        sub.prop(s, "pre_decimate_max_faces", text="Max")


# ---------------------------------------------------------------------------
# Symmetry
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_symmetry(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_symmetry"
    bl_label = "Symmetry"

    def draw_header(self, context):
        self.layout.prop(context.scene.auto_retopo, "use_symmetry", text="")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo
        layout.active = s.use_symmetry

        layout.row().prop(s, "symmetry_axis", expand=True)
        layout.row().prop(s, "symmetry_side", expand=True)
        layout.prop(s, "symmetry_symmetrize_source")
        layout.prop(s, "symmetry_merge")
        layout.prop(s, "symmetry_apply")
        _tip(layout, context, "Mirror plane passes through the object origin")


# ---------------------------------------------------------------------------
# Projection & Relax
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_projection(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_projection"
    bl_label = "Relax & Projection"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        col = layout.column(align=True)
        col.prop(s, "relax_iterations")
        sub = col.column(align=True)
        sub.active = s.relax_iterations > 0
        sub.prop(s, "relax_factor", slider=True)
        col = layout.column()
        col.active = s.relax_iterations > 0
        col.prop(s, "relax_lock_boundary")
        layout.prop(s, "relax_reproject")
        layout.prop(s, "projection_target")

        layout.separator()
        layout.prop(s, "use_shrinkwrap")
        col = layout.column()
        col.active = s.use_shrinkwrap
        col.prop(s, "sw_method")
        sub = col.column()
        sub.active = s.sw_method != 'NEAREST_VERTEX'
        sub.prop(s, "sw_mode")
        col.prop(s, "sw_offset")
        col.prop(s, "sw_apply")


# ---------------------------------------------------------------------------
# Topology & shading
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_cleanup(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_cleanup"
    bl_label = "Topology & Shading"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        layout.prop(s, "post_tris_to_quads")
        col = layout.column(align=True)
        col.active = s.post_tris_to_quads or (s.method == 'DECIMATE' and s.dc_tris_to_quads)
        col.prop(s, "post_quad_face_angle")
        col.prop(s, "post_quad_shape_angle")

        layout.prop(s, "post_dissolve_degenerate")
        layout.prop(s, "post_merge_distance")
        layout.prop(s, "post_recalc_normals")

        row = layout.row(heading="Triangulate")
        row.prop(s, "post_triangulate", text="")
        sub = row.row()
        sub.active = s.post_triangulate
        sub.prop(s, "post_triangulate_method", text="")

        layout.separator()
        layout.prop(s, "shading")
        sub = layout.column()
        sub.active = s.shading == 'AUTO'
        sub.prop(s, "auto_smooth_angle")


# ---------------------------------------------------------------------------
# UVs & data
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_data(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_data"
    bl_label = "UVs & Data Transfer"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        layout.prop(s, "uv_mode")
        if s.uv_mode == 'SMART':
            layout.prop(s, "uv_angle_limit")
        if s.uv_mode in {'SMART', 'LIGHTMAP'}:
            layout.prop(s, "uv_island_margin")

        col = layout.column(heading="Transfer")
        col.prop(s, "transfer_colors")
        col.prop(s, "transfer_vgroups")
        col.prop(s, "transfer_normals")
        sub = layout.column()
        sub.active = s.uv_mode == 'TRANSFER' or s.transfer_colors or s.transfer_vgroups \
            or s.transfer_normals
        sub.prop(s, "transfer_apply")
        layout.prop(s, "copy_materials")


# ---------------------------------------------------------------------------
# Subdivision
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_subdiv(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_subdiv"
    bl_label = "Subdivision & Detail"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        layout.prop(s, "subdiv_mode")
        col = layout.column(align=True)
        col.active = s.subdiv_mode != 'NONE'
        col.prop(s, "subdiv_levels")
        if s.subdiv_mode == 'SUBSURF':
            col.prop(s, "subdiv_render_levels")
        if s.subdiv_mode == 'MULTIRES':
            _tip(layout, context, "Detail is projected from the original surface")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_output(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_output"
    bl_label = "Output"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        s = context.scene.auto_retopo

        layout.row().prop(s, "output_mode", expand=True)
        if s.output_mode == 'REPLACE':
            _tip(layout, context, "Destructive: all modifiers get applied", icon='ERROR')
            return
        layout.prop(s, "name_suffix")
        layout.prop_search(s, "output_collection", bpy.data, "collections")
        layout.prop(s, "replace_previous")
        layout.prop(s, "original_display")
        col = layout.column(heading="Result")
        col.prop(s, "show_wire")
        col.prop(s, "show_in_front")


# ---------------------------------------------------------------------------
# Tools & analysis
# ---------------------------------------------------------------------------

class AUTORETOPO_PT_tools(AutoRetopoSubPanel, Panel):
    bl_idname = "AUTORETOPO_PT_tools"
    bl_label = "Tools & Analysis"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("auto_retopo.toggle_original", icon='HIDE_OFF')
        col.operator("auto_retopo.apply_modifiers", icon='CHECKMARK')
        col.operator("auto_retopo.manual_setup", icon='MESH_PLANE')

        layout.separator()
        layout.operator("auto_retopo.analyze", icon='VIEWZOOM')
        st = context.scene.auto_retopo_stats
        if not st.object_name:
            return
        box = layout.box()
        box.label(text=st.object_name, icon='OBJECT_DATA')
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)

        def stat(label, value):
            grid.label(text=label)
            grid.label(text=value)

        faces = max(st.faces, 1)
        stat("Vertices", f"{st.verts:,}")
        stat("Faces", f"{st.faces:,}")
        stat("Quads", f"{st.quads:,} ({100.0 * st.quads / faces:.1f}%)")
        stat("Triangles", f"{st.tris:,}")
        stat("N-gons", f"{st.ngons:,}")
        stat("Poles", f"{st.poles:,}")
        stat("Islands", f"{st.islands:,}")
        stat("Boundary Edges", f"{st.boundary:,}")
        stat("Non-manifold", f"{st.non_manifold:,}")
        stat("Avg. Edge", bpy.utils.units.to_string(
            context.scene.unit_settings.system, 'LENGTH', st.avg_edge, precision=4))
        if st.non_manifold:
            box.label(text="Non-manifold edges: enable Voxel Cleanup", icon='ERROR')


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

class AUTORETOPO_MT_object(Menu):
    bl_idname = "AUTORETOPO_MT_object"
    bl_label = "Auto Retopology"

    def draw(self, context):
        layout = self.layout
        layout.operator("auto_retopo.run", icon='MOD_REMESH')
        layout.menu(AUTORETOPO_MT_quick_presets.bl_idname, icon='SHADERFX')
        layout.separator()
        layout.operator("auto_retopo.toggle_original", icon='HIDE_OFF')
        layout.operator("auto_retopo.apply_modifiers", icon='CHECKMARK')
        layout.operator("auto_retopo.manual_setup", icon='MESH_PLANE')
        layout.operator("auto_retopo.analyze", icon='VIEWZOOM')


def menu_func(self, context):
    self.layout.separator()
    self.layout.menu(AUTORETOPO_MT_object.bl_idname, icon='MOD_REMESH')


panels = (
    AUTORETOPO_PT_main,
    AUTORETOPO_PT_method,
    AUTORETOPO_PT_preprocess,
    AUTORETOPO_PT_symmetry,
    AUTORETOPO_PT_projection,
    AUTORETOPO_PT_cleanup,
    AUTORETOPO_PT_data,
    AUTORETOPO_PT_subdiv,
    AUTORETOPO_PT_output,
    AUTORETOPO_PT_tools,
)

menus = (AUTORETOPO_MT_object,)


def update_category(category):
    """Move all panels to another sidebar tab."""
    category = category.strip() or DEFAULT_CATEGORY
    for cls in panels:
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    for cls in panels:
        cls.bl_category = category
        bpy.utils.register_class(cls)


def register(category=DEFAULT_CATEGORY):
    for cls in menus:
        bpy.utils.register_class(cls)
    for cls in panels:
        cls.bl_category = category or DEFAULT_CATEGORY
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object.append(menu_func)
    bpy.types.VIEW3D_MT_object_context_menu.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu_func)
    bpy.types.VIEW3D_MT_object.remove(menu_func)
    for cls in reversed(panels):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    for cls in reversed(menus):
        bpy.utils.unregister_class(cls)
