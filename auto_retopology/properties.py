# SPDX-License-Identifier: GPL-3.0-or-later
"""All user-facing settings of the Auto Retopology add-on.

Settings live on the scene (``scene.auto_retopo``) so they are saved with the
.blend file and can be stored in presets.
"""

import math

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


# ---------------------------------------------------------------------------
# Enum items
# ---------------------------------------------------------------------------

METHOD_ITEMS = [
    ('QUADRIFLOW', "Quad Remesh (QuadriFlow)",
     "Field-aligned all-quad remeshing. Best general purpose result", 'MESH_GRID', 0),
    ('VOXEL', "Voxel Remesh",
     "Fast, uniform quad grid that always produces a watertight mesh", 'MESH_CUBE', 1),
    ('DECIMATE', "Decimate",
     "Reduce the existing topology (keeps UVs and attributes best)", 'MOD_DECIM', 2),
    ('OCTREE', "Octree Remesh",
     "Classic Remesh modifier: Blocks, Smooth or Sharp modes", 'MOD_REMESH', 3),
]

DENSITY_ITEMS = [
    ('FACES', "Face Count", "Aim for an absolute number of output faces"),
    ('RATIO', "Ratio", "Aim for a fraction of the input face count"),
    ('EDGE_LENGTH', "Edge Length", "Aim for an average edge length (in scene units)"),
]

AXIS_ITEMS = [
    ('X', "X", "Mirror across the object's local YZ plane"),
    ('Y', "Y", "Mirror across the object's local XZ plane"),
    ('Z', "Z", "Mirror across the object's local XY plane"),
]

SIDE_ITEMS = [
    ('POSITIVE', "+ Side", "Keep the positive side and mirror it to the negative side"),
    ('NEGATIVE', "- Side", "Keep the negative side and mirror it to the positive side"),
]

OUTPUT_MODE_ITEMS = [
    ('NEW_OBJECT', "New Object", "Create a new object next to the original"),
    ('REPLACE', "Replace Mesh",
     "Replace the original object's mesh data (all modifiers are applied)"),
]

ORIGINAL_DISPLAY_ITEMS = [
    ('KEEP', "Keep", "Leave the original object untouched"),
    ('HIDE', "Hide", "Hide the original object in the viewport"),
    ('WIRE', "Wire", "Display the original as wireframe"),
    ('BOUNDS', "Bounds", "Display the original as bounding box"),
]

DECIMATE_MODE_ITEMS = [
    ('COLLAPSE', "Collapse", "Edge collapse driven by the density target"),
    ('UNSUBDIV', "Un-Subdivide", "Reverse subdivision on grid-like topology"),
    ('DISSOLVE', "Planar", "Dissolve geometry that is flatter than an angle"),
]

OCTREE_MODE_ITEMS = [
    ('BLOCKS', "Blocks", "Output a blocky, voxel-art like surface"),
    ('SMOOTH', "Smooth", "Output a smooth surface"),
    ('SHARP', "Sharp", "Output a surface that keeps sharp edges and corners"),
]

SHRINKWRAP_METHOD_ITEMS = [
    ('NEAREST_SURFACEPOINT', "Nearest Surface Point", "Move each vertex to the closest surface point"),
    ('PROJECT', "Project", "Project vertices along their normals (both directions)"),
    ('NEAREST_VERTEX', "Nearest Vertex", "Snap to the closest source vertex"),
    ('TARGET_PROJECT', "Target Normal Project",
     "Project using the interpolated normals of the source surface"),
]

SHRINKWRAP_MODE_ITEMS = [
    ('ON_SURFACE', "On Surface", "Always place vertices on the surface"),
    ('OUTSIDE_SURFACE', "Outside Surface", "Place vertices outside of the surface"),
    ('ABOVE_SURFACE', "Above Surface", "Place vertices on the surface plus the offset along the normal"),
    ('INSIDE', "Inside", "Only move vertices that are outside of the surface"),
    ('OUTSIDE', "Outside", "Only move vertices that are inside of the surface"),
]

SHADING_ITEMS = [
    ('KEEP', "Keep", "Keep whatever shading the algorithm produced"),
    ('FLAT', "Flat", "Flat shading"),
    ('SMOOTH', "Smooth", "Smooth shading everywhere"),
    ('AUTO', "Auto Smooth", "Smooth shading, with edges sharper than an angle marked sharp"),
]

UV_ITEMS = [
    ('NONE', "None", "Do not create UVs"),
    ('TRANSFER', "Transfer", "Transfer UV maps from the original mesh"),
    ('SMART', "Smart UV Project", "Automatic island-based unwrap"),
    ('LIGHTMAP', "Lightmap Pack", "One island per face, packed - good for lightmaps"),
]

SUBDIV_ITEMS = [
    ('NONE', "None", "No subdivision"),
    ('SUBSURF', "Subdivision Surface", "Add a Subdivision Surface modifier for a smooth preview"),
    ('MULTIRES', "Multires (Bake Detail)",
     "Add a Multiresolution modifier and project the original detail into it - "
     "ready for sculpting or normal-map baking"),
]

TRIANGULATE_QUAD_ITEMS = [
    ('BEAUTY', "Beauty", "Split quads in a nice way"),
    ('FIXED', "Fixed", "Split along the first and third vertex"),
    ('SHORT_EDGE', "Shortest Diagonal", "Split along the shortest diagonal"),
    ('LONG_EDGE', "Longest Diagonal", "Split along the longest diagonal"),
]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class AutoRetopoSettings(PropertyGroup):
    # ----- Core ------------------------------------------------------------
    method: EnumProperty(
        name="Method", items=METHOD_ITEMS, default='QUADRIFLOW',
        description="Algorithm used to build the new topology",
    )
    density_mode: EnumProperty(
        name="Density", items=DENSITY_ITEMS, default='FACES',
        description="How the target resolution is specified",
    )
    target_faces: IntProperty(
        name="Target Faces", default=5000, min=8, soft_max=200000,
        description="Approximate number of faces of the result",
    )
    target_ratio: FloatProperty(
        name="Ratio", default=0.1, min=0.0001, max=1.0, subtype='FACTOR', precision=4,
        description="Result face count as a fraction of the input face count",
    )
    target_edge_length: FloatProperty(
        name="Edge Length", default=0.05, min=0.0001, soft_max=10.0,
        subtype='DISTANCE', precision=4,
        description="Average edge length of the result",
    )
    process_all_selected: BoolProperty(
        name="All Selected", default=True,
        description="Process every selected mesh (otherwise only the active object)",
    )

    # ----- Pre-processing --------------------------------------------------
    apply_modifiers: BoolProperty(
        name="Apply Modifiers", default=True,
        description="Use the evaluated mesh (with modifiers) of the original as input",
    )
    use_world_scale: BoolProperty(
        name="Use World Scale", default=True,
        description="Bake object scale into the mesh so densities are measured in world units. "
                    "The result gets unit scale",
    )
    pre_merge: BoolProperty(
        name="Merge by Distance", default=True,
        description="Weld vertices that are closer than the merge distance",
    )
    pre_merge_distance: FloatProperty(
        name="Distance", default=0.0001, min=0.0, soft_max=0.1, subtype='DISTANCE', precision=5,
    )
    pre_delete_loose: BoolProperty(
        name="Delete Loose", default=True,
        description="Remove vertices and edges that are not part of any face",
    )
    pre_remove_small_parts: BoolProperty(
        name="Remove Small Parts", default=False,
        description="Remove disconnected islands that have fewer faces than the threshold",
    )
    pre_small_part_faces: IntProperty(
        name="Min Faces", default=16, min=1, soft_max=10000,
        description="Islands with fewer faces than this are removed",
    )
    pre_fill_holes: BoolProperty(
        name="Fill Holes", default=False,
        description="Close open boundaries before remeshing",
    )
    pre_fill_holes_sides: IntProperty(
        name="Max Sides", default=0, min=0, soft_max=1000,
        description="Only fill holes with at most this many edges (0 = fill all holes)",
    )
    pre_recalc_normals: BoolProperty(
        name="Recalculate Normals", default=True,
        description="Make face normals consistent and pointing outwards",
    )
    pre_smooth_iterations: IntProperty(
        name="Pre-Smooth", default=0, min=0, soft_max=50,
        description="Smooth the input before remeshing to remove noise (scans, sculpt artifacts)",
    )
    pre_smooth_factor: FloatProperty(
        name="Factor", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
    )
    pre_voxel_cleanup: BoolProperty(
        name="Voxel Cleanup", default=True,
        description="Run a fine voxel remesh first. Makes any input manifold and watertight, "
                    "which QuadriFlow requires",
    )
    pre_voxel_detail: FloatProperty(
        name="Cleanup Detail", default=4.0, min=1.0, soft_max=16.0,
        description="Resolution of the cleanup pass relative to the target edge length "
                    "(higher keeps more detail but is slower)",
    )
    pre_decimate: BoolProperty(
        name="Pre-Decimate Heavy Meshes", default=True,
        description="Decimate very dense inputs before remeshing to speed things up",
    )
    pre_decimate_max_faces: IntProperty(
        name="Max Input Faces", default=300000, min=1000, soft_max=5000000,
        description="Inputs above this face count are decimated down to it first",
    )

    # ----- QuadriFlow -------------------------------------------------------
    qf_preserve_sharp: BoolProperty(
        name="Preserve Sharp", default=False,
        description="Try to keep sharp features of the input",
    )
    qf_preserve_boundary: BoolProperty(
        name="Preserve Boundary", default=False,
        description="Try to keep open mesh boundaries",
    )
    qf_preserve_attributes: BoolProperty(
        name="Preserve Attributes", default=False,
        description="Reproject sculpt mask, face sets and color attributes",
    )
    qf_smooth_normals: BoolProperty(
        name="Smooth Normals", default=False,
        description="Set the output mesh normals to smooth",
    )
    qf_native_symmetry: BoolProperty(
        name="Native Symmetry", default=False,
        description="Also use QuadriFlow's own symmetry mode when Symmetry is enabled "
                    "(more mirrored edge flow, but can leave a messier seam)",
    )
    qf_seed: IntProperty(
        name="Seed", default=0, min=0,
        description="Random seed. Different seeds give different edge flows",
    )
    qf_retries: IntProperty(
        name="Retries", default=2, min=0, max=10,
        description="If QuadriFlow fails, retry with another seed this many times",
    )

    # ----- Voxel ------------------------------------------------------------
    vx_adaptivity: FloatProperty(
        name="Adaptivity", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
        description="Reduce polygons in flat areas (produces triangles)",
    )
    vx_fix_poles: BoolProperty(
        name="Fix Poles", default=True,
        description="Produce fewer poles and a better topology flow",
    )
    vx_preserve_volume: BoolProperty(
        name="Preserve Volume", default=True,
        description="Project the result onto the original surface to keep its volume",
    )
    vx_preserve_attributes: BoolProperty(
        name="Preserve Attributes", default=False,
        description="Keep sculpt mask, face sets and color attributes",
    )

    # ----- Decimate ---------------------------------------------------------
    dc_mode: EnumProperty(name="Decimate Type", items=DECIMATE_MODE_ITEMS, default='COLLAPSE')
    dc_triangulate: BoolProperty(
        name="Triangulate", default=False,
        description="Keep triangulated faces produced by the collapse",
    )
    dc_tris_to_quads: BoolProperty(
        name="Convert to Quads", default=True,
        description="Join the resulting triangles into quads (target face count accounts for it)",
    )
    dc_iterations: IntProperty(
        name="Iterations", default=2, min=1, max=16,
        description="Un-Subdivide iterations",
    )
    dc_angle_limit: FloatProperty(
        name="Angle Limit", default=math.radians(5.0), min=0.0, max=math.pi,
        subtype='ANGLE', description="Planar dissolve angle",
    )
    dc_delimit: EnumProperty(
        name="Delimit", options={'ENUM_FLAG'}, default={'NORMAL'},
        items=[
            ('NORMAL', "Normal", "Delimit by face normals"),
            ('MATERIAL', "Material", "Delimit by material"),
            ('SEAM', "Seam", "Delimit by UV seams"),
            ('SHARP', "Sharp", "Delimit by sharp edges"),
            ('UV', "UVs", "Delimit by UV coordinates"),
        ],
    )

    # ----- Octree -----------------------------------------------------------
    oc_mode: EnumProperty(name="Octree Mode", items=OCTREE_MODE_ITEMS, default='SMOOTH')
    oc_auto_depth: BoolProperty(
        name="Depth from Density", default=True,
        description="Pick the octree depth from the density target",
    )
    oc_depth: IntProperty(name="Octree Depth", default=6, min=1, max=12)
    oc_scale: FloatProperty(name="Scale", default=0.9, min=0.01, max=0.99, subtype='FACTOR')
    oc_sharpness: FloatProperty(name="Sharpness", default=1.0, min=0.0, soft_max=10.0)
    oc_remove_disconnected: BoolProperty(
        name="Remove Disconnected", default=True,
        description="Remove small disconnected pieces of the output",
    )
    oc_threshold: FloatProperty(name="Threshold", default=1.0, min=0.0, max=1.0, subtype='FACTOR')

    # ----- Symmetry ---------------------------------------------------------
    use_symmetry: BoolProperty(
        name="Symmetry", default=False,
        description="Produce a perfectly symmetric result",
    )
    symmetry_axis: EnumProperty(name="Axis", items=AXIS_ITEMS, default='X')
    symmetry_side: EnumProperty(name="Keep", items=SIDE_ITEMS, default='POSITIVE')
    symmetry_symmetrize_source: BoolProperty(
        name="Symmetrize Input", default=True,
        description="Make the input symmetric before remeshing (cleaner center line)",
    )
    symmetry_merge: FloatProperty(
        name="Merge Distance", default=0.001, min=0.0, soft_max=0.1, subtype='DISTANCE', precision=4,
        description="Vertices this close to the mirror plane are snapped onto it and merged",
    )
    symmetry_apply: BoolProperty(
        name="Apply Mirror", default=True,
        description="Apply the Mirror modifier (otherwise keep it live for editing one half)",
    )

    # ----- Relax & projection ----------------------------------------------
    relax_iterations: IntProperty(
        name="Relax Iterations", default=3, min=0, soft_max=50,
        description="Even out the vertex distribution while staying on the original surface",
    )
    relax_factor: FloatProperty(name="Relax Strength", default=0.5, min=0.0, max=1.0, subtype='FACTOR')
    relax_lock_boundary: BoolProperty(
        name="Lock Boundary", default=True,
        description="Do not move open boundary vertices while relaxing",
    )
    relax_reproject: BoolProperty(
        name="Stick to Surface", default=True,
        description="Project vertices back onto the original after every relax step",
    )
    projection_target: EnumProperty(
        name="Project Onto", default='AUTO',
        items=[
            ('AUTO', "Hybrid", "Find the outer surface on a watertight voxel shell, then snap "
                               "to the exact original surface close to it. Precise, and never "
                               "lands on hidden inner geometry"),
            ('SHELL', "Outer Shell",
             "A watertight voxel shell of the original. Stops vertices snapping onto hidden "
             "inner geometry (overlapping parts, eyes in sockets, double walls)"),
            ('ORIGINAL', "Original Surface", "The original mesh exactly as it is"),
        ],
        description="Surface used by relax re-projection, shrinkwrap and multires baking "
                    "(Decimate always uses the original surface)",
    )
    use_shrinkwrap: BoolProperty(
        name="Shrinkwrap", default=True,
        description="Project the result onto the original surface",
    )
    sw_method: EnumProperty(name="Wrap Method", items=SHRINKWRAP_METHOD_ITEMS, default='NEAREST_SURFACEPOINT')
    sw_mode: EnumProperty(name="Snap Mode", items=SHRINKWRAP_MODE_ITEMS, default='ON_SURFACE')
    sw_offset: FloatProperty(
        name="Offset", default=0.0, soft_min=-1.0, soft_max=1.0, subtype='DISTANCE', precision=4,
        description="Distance kept from the original surface",
    )
    sw_apply: BoolProperty(
        name="Apply Shrinkwrap", default=True,
        description="Apply the modifier (otherwise keep it live, targeting the original object)",
    )

    # ----- Topology cleanup ------------------------------------------------
    post_tris_to_quads: BoolProperty(
        name="Triangles to Quads", default=True,
        description="Join leftover triangle pairs into quads",
    )
    post_quad_face_angle: FloatProperty(
        name="Max Face Angle", default=math.radians(40.0), min=0.0, max=math.pi, subtype='ANGLE',
    )
    post_quad_shape_angle: FloatProperty(
        name="Max Shape Angle", default=math.radians(40.0), min=0.0, max=math.pi, subtype='ANGLE',
    )
    post_dissolve_degenerate: BoolProperty(
        name="Dissolve Degenerate", default=True,
        description="Remove zero-area faces and zero-length edges",
    )
    post_merge_distance: FloatProperty(
        name="Weld Distance", default=0.0, min=0.0, soft_max=0.1, subtype='DISTANCE', precision=5,
        description="Weld result vertices closer than this (0 = disabled)",
    )
    post_recalc_normals: BoolProperty(name="Recalculate Normals", default=True)
    post_triangulate: BoolProperty(
        name="Triangulate Result", default=False,
        description="Triangulate the final mesh (e.g. for game engines)",
    )
    post_triangulate_method: EnumProperty(name="Quad Method", items=TRIANGULATE_QUAD_ITEMS, default='BEAUTY')

    # ----- Shading & UVs ----------------------------------------------------
    shading: EnumProperty(name="Shading", items=SHADING_ITEMS, default='AUTO')
    auto_smooth_angle: FloatProperty(
        name="Sharp Angle", default=math.radians(30.0), min=0.0, max=math.pi, subtype='ANGLE',
    )
    uv_mode: EnumProperty(name="UVs", items=UV_ITEMS, default='NONE')
    uv_angle_limit: FloatProperty(
        name="Angle Limit", default=math.radians(66.0), min=0.0, max=math.radians(89.0), subtype='ANGLE',
    )
    uv_island_margin: FloatProperty(name="Island Margin", default=0.01, min=0.0, max=1.0)
    transfer_colors: BoolProperty(
        name="Color Attributes", default=False, description="Transfer color attributes from the original",
    )
    transfer_vgroups: BoolProperty(
        name="Vertex Groups", default=False, description="Transfer vertex group weights from the original",
    )
    transfer_normals: BoolProperty(
        name="Custom Normals", default=False,
        description="Transfer the shading normals of the original (makes the low poly look high poly)",
    )
    transfer_apply: BoolProperty(
        name="Apply Transfer", default=True,
        description="Apply the Data Transfer modifier (otherwise keep it live)",
    )
    copy_materials: BoolProperty(name="Copy Materials", default=True)

    # ----- Subdivision -----------------------------------------------------
    subdiv_mode: EnumProperty(name="Subdivision", items=SUBDIV_ITEMS, default='NONE')
    subdiv_levels: IntProperty(name="Levels", default=1, min=1, max=6)
    subdiv_render_levels: IntProperty(name="Render Levels", default=2, min=0, max=6)

    # ----- Output -----------------------------------------------------------
    output_mode: EnumProperty(name="Output", items=OUTPUT_MODE_ITEMS, default='NEW_OBJECT')
    name_suffix: StringProperty(name="Suffix", default="_retopo")
    output_collection: StringProperty(
        name="Collection", default="",
        description="Put results into this collection (created if needed). Empty = same as original",
    )
    replace_previous: BoolProperty(
        name="Replace Previous Result", default=True,
        description="Delete an earlier result of the same original before creating a new one",
    )
    original_display: EnumProperty(name="Original", items=ORIGINAL_DISPLAY_ITEMS, default='HIDE')
    show_wire: BoolProperty(name="Show Wireframe", default=True, description="Overlay the result's wireframe")
    show_in_front: BoolProperty(name="In Front", default=False, description="Draw the result in front of others")


class AutoRetopoStats(PropertyGroup):
    """Last analysis result shown in the Analysis panel."""
    object_name: StringProperty()
    verts: IntProperty()
    faces: IntProperty()
    tris: IntProperty()
    quads: IntProperty()
    ngons: IntProperty()
    poles: IntProperty()
    non_manifold: IntProperty()
    boundary: IntProperty()
    islands: IntProperty()
    area: FloatProperty(unit='AREA')
    avg_edge: FloatProperty(subtype='DISTANCE')


classes = (AutoRetopoSettings, AutoRetopoStats)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.auto_retopo = PointerProperty(type=AutoRetopoSettings)
    bpy.types.Scene.auto_retopo_stats = PointerProperty(type=AutoRetopoStats)
    bpy.types.Object.auto_retopo_source = PointerProperty(
        type=bpy.types.Object, name="Retopo Source",
        description="Original object this retopology was generated from",
    )


def unregister():
    del bpy.types.Object.auto_retopo_source
    del bpy.types.Scene.auto_retopo_stats
    del bpy.types.Scene.auto_retopo
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
