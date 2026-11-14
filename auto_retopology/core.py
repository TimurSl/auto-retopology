# SPDX-License-Identifier: GPL-3.0-or-later
"""Retopology pipeline. Pure functions + one ``run_retopology`` entry point.

Nothing in here touches the UI, so the pipeline can also be driven from
scripts::

    from auto_retopology import core
    core.run_retopology(bpy.context, obj, bpy.context.scene.auto_retopo)

Pipeline overview
-----------------
1. Build a *reference* mesh from the original (modifiers, world scale),
   clean it up (merge, loose parts, holes, normals, smoothing, symmetrize).
2. Copy it into a *work* object and run the chosen algorithm
   (QuadriFlow / Voxel / Decimate / Octree), with optional voxel cleanup and
   pre-decimation.
3. Topology cleanup, symmetry cut, tangential relax with re-projection.
4. Shrinkwrap, mirror, triangulate, shading, UVs, data transfer.
5. Multires detail bake / Subdivision preview, live modifiers, output.
"""

import math
import time

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree


class RetopoError(Exception):
    """A user-facing failure (bad input, failed remesh, ...)."""


AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}
PREFIX = "AR_"
QUADRIFLOW_SIZE = 100.0  # bounding size QuadriFlow input is normalized to


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def override(obj, *extra):
    """Context override that makes ``obj`` the active & selected object."""
    sel = [obj, *extra]
    return dict(object=obj, active_object=obj,
                selected_objects=sel, selected_editable_objects=sel)


def apply_all_modifiers(context, obj):
    """Bake the whole modifier stack of ``obj`` into its mesh data."""
    if not obj.modifiers:
        return
    dg = context.evaluated_depsgraph_get()
    new_me = bpy.data.meshes.new_from_object(
        obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    old = obj.data
    name = old.name
    obj.modifiers.clear()
    obj.data = new_me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    new_me.name = name


def mesh_bounds_max_dim(me):
    n = len(me.vertices)
    if n == 0:
        return 0.0
    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co.shape = (n, 3)
    return float((co.max(axis=0) - co.min(axis=0)).max())


def bm_area(bm):
    return sum(f.calc_area() for f in bm.faces)


def bm_tri_count(bm):
    return sum(len(f.verts) - 2 for f in bm.faces)


def mesh_tri_count(me):
    n = len(me.polygons)
    if n == 0:
        return 0
    counts = np.empty(n, dtype=np.int32)
    me.polygons.foreach_get("loop_total", counts)
    return int((counts - 2).sum())


def face_islands(bm):
    """Return a list of face lists, one per edge-connected island."""
    for f in bm.faces:
        f.tag = False
    islands = []
    for start in bm.faces:
        if start.tag:
            continue
        start.tag = True
        stack = [start]
        island = []
        while stack:
            f = stack.pop()
            island.append(f)
            for e in f.edges:
                for nf in e.link_faces:
                    if not nf.tag:
                        nf.tag = True
                        stack.append(nf)
        islands.append(island)
    for f in bm.faces:
        f.tag = False
    return islands


def copy_vertex_group_names(src, dst):
    if src is None or src.type != 'MESH':
        return
    dst.vertex_groups.clear()
    for vg in src.vertex_groups:
        dst.vertex_groups.new(name=vg.name)


def remove_object(obj):
    me = obj.data if obj.type == 'MESH' else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def get_output_collection(context, original, name):
    if name:
        coll = bpy.data.collections.get(name)
        if coll is None:
            coll = bpy.data.collections.new(name)
        scene_colls = {context.scene.collection, *context.scene.collection.children_recursive}
        if coll not in scene_colls:
            context.scene.collection.children.link(coll)
        return coll
    for coll in original.users_collection:
        return coll
    return context.scene.collection


# ---------------------------------------------------------------------------
# Density
# ---------------------------------------------------------------------------

def compute_density(s, face_count, area):
    """Return ``(target_faces, target_edge_length)`` for the settings."""
    area = max(area, 1e-12)
    if s.density_mode == 'FACES':
        target = s.target_faces
    elif s.density_mode == 'RATIO':
        target = max(8, int(round(face_count * s.target_ratio)))
    else:
        target = max(8, int(round(area / (s.target_edge_length ** 2))))
    if s.density_mode == 'EDGE_LENGTH':
        edge = s.target_edge_length
    else:
        edge = math.sqrt(area / max(target, 1))
    return target, edge


# ---------------------------------------------------------------------------
# Mesh statistics
# ---------------------------------------------------------------------------

def mesh_stats(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        tris = quads = ngons = 0
        for f in bm.faces:
            n = len(f.verts)
            if n == 3:
                tris += 1
            elif n == 4:
                quads += 1
            else:
                ngons += 1
        poles = sum(1 for v in bm.verts
                    if v.link_faces and v.is_manifold and not v.is_boundary
                    and len(v.link_edges) != 4)
        non_manifold = sum(1 for e in bm.edges if len(e.link_faces) > 2 or not e.link_faces)
        boundary = sum(1 for e in bm.edges if e.is_boundary)
        avg_edge = (sum(e.calc_length() for e in bm.edges) / len(bm.edges)) if bm.edges else 0.0
        return dict(
            verts=len(bm.verts), faces=len(bm.faces), tris=tris, quads=quads, ngons=ngons,
            poles=poles, non_manifold=non_manifold, boundary=boundary,
            islands=len(face_islands(bm)), area=bm_area(bm), avg_edge=avg_edge,
            tri_count=bm_tri_count(bm),
        )
    finally:
        bm.free()


# ---------------------------------------------------------------------------
# BMesh stages
# ---------------------------------------------------------------------------

def preprocess_bmesh(bm, s, warnings):
    if s.pre_merge and s.pre_merge_distance > 0.0:
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=s.pre_merge_distance)

    if s.pre_delete_loose:
        delete_loose(bm)

    if s.pre_remove_small_parts:
        islands = face_islands(bm)
        if len(islands) > 1:
            islands.sort(key=len, reverse=True)
            doomed = [f for isl in islands[1:] if len(isl) < s.pre_small_part_faces for f in isl]
            if doomed:
                bmesh.ops.delete(bm, geom=doomed, context='FACES')

    if s.pre_fill_holes:
        bmesh.ops.holes_fill(bm, edges=bm.edges[:], sides=s.pre_fill_holes_sides)

    if s.pre_recalc_normals:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

    if s.pre_smooth_iterations > 0:
        verts = [v for v in bm.verts if not v.is_boundary]
        for _ in range(s.pre_smooth_iterations):
            bmesh.ops.smooth_vert(bm, verts=verts, factor=s.pre_smooth_factor,
                                  use_axis_x=True, use_axis_y=True, use_axis_z=True)

    if s.use_symmetry and s.symmetry_symmetrize_source:
        direction = s.symmetry_axis if s.symmetry_side == 'POSITIVE' else '-' + s.symmetry_axis
        bmesh.ops.symmetrize(bm, input=bm.verts[:] + bm.edges[:] + bm.faces[:],
                             direction=direction, dist=s.symmetry_merge)
        if s.pre_recalc_normals:
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])


def delete_loose(bm):
    """Remove wire edges and vertices that are not part of any face."""
    loose_edges = [e for e in bm.edges if not e.link_faces]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context='EDGES')
    loose_verts = [v for v in bm.verts if not v.link_faces]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context='VERTS')


def remove_duplicate_faces(bm):
    seen = set()
    dupes = []
    for f in bm.faces:
        key = frozenset(f.verts)
        if key in seen:
            dupes.append(f)
        else:
            seen.add(key)
    if dupes:
        bmesh.ops.delete(bm, geom=dupes, context='FACES_ONLY')
    return len(dupes)


def tris_to_quads(bm, face_angle, shape_angle):
    bmesh.ops.join_triangles(
        bm, faces=[f for f in bm.faces if len(f.verts) == 3],
        angle_face_threshold=face_angle, angle_shape_threshold=shape_angle,
        cmp_seam=True, cmp_materials=True)


def symmetry_cut(bm, axis, side, merge, edge=0.0):
    """Cut the mesh on the mirror plane and keep one side.

    Vertices next to the seam that end up closer to the plane than ~1/3 of an
    edge are snapped onto it, which removes the sliver row the cut leaves
    behind (otherwise visible as a crease once mirrored).
    """
    no = Vector((0.0, 0.0, 0.0))
    no[axis] = 1.0
    eps = max(merge, 1e-6)
    bmesh.ops.bisect_plane(
        bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=eps,
        plane_co=(0.0, 0.0, 0.0), plane_no=no,
        clear_inner=(side == 'POSITIVE'), clear_outer=(side == 'NEGATIVE'))

    def on_plane(v):
        return abs(v.co[axis]) <= eps

    for v in bm.verts:
        if on_plane(v):
            v.co[axis] = 0.0
    snap = edge * 0.35
    if snap > eps:
        center = {v for v in bm.verts if on_plane(v)}
        near = {e.other_vert(v) for v in center for e in v.link_edges}
        for v in near:
            if v not in center and abs(v.co[axis]) < snap:
                v.co[axis] = 0.0
    center = [v for v in bm.verts if abs(v.co[axis]) <= eps]
    if center:
        bmesh.ops.remove_doubles(bm, verts=center, dist=max(eps, edge * 0.05))
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=max(eps, edge * 0.01))
        # Faces that collapsed entirely onto the plane would block the mirror merge.
        flat = [f for f in bm.faces if all(abs(v.co[axis]) <= eps for v in f.verts)]
        if flat:
            bmesh.ops.delete(bm, geom=flat, context='FACES_ONLY')
        delete_loose(bm)
    if not bm.faces:
        raise RetopoError("Nothing is left on the kept side of the mirror plane. "
                          "Check the symmetry axis/side (the plane passes through the object origin)")


def make_projector(primary, secondary=None, max_dist=0.0):
    """Return ``f(co) -> Vector | None`` projecting onto ``primary`` (a BVHTree).

    With ``secondary`` the point found on ``primary`` is refined to the nearest
    point of ``secondary`` if one lies within ``max_dist``.
    """
    def project(co):
        loc = primary.find_nearest(co)[0]
        if loc is not None and secondary is not None:
            fine = secondary.find_nearest(loc, max_dist)[0]
            if fine is not None:
                return fine
        return loc
    return project


def bvh_from_mesh(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        return BVHTree.FromBMesh(bm)
    finally:
        bm.free()


def relax_bmesh(bm, project_fn, iterations, factor, lock_boundary, reproject,
                sym_axis=None, sym_eps=1e-5):
    """Tangential Laplacian relax with optional projection via ``project_fn``."""
    if iterations <= 0 and not (reproject and project_fn):
        return
    center = set()
    if sym_axis is not None:
        center = {v for v in bm.verts if abs(v.co[sym_axis]) <= sym_eps}
    verts = [v for v in bm.verts
             if v.link_faces and (v in center or not (lock_boundary and v.is_boundary))]

    def project():
        if not (reproject and project_fn):
            return
        for v in verts:
            loc = project_fn(v.co)
            if loc is not None:
                v.co = loc

    def clamp_center():
        for v in center:
            v.co[sym_axis] = 0.0

    for _ in range(iterations):
        bm.normal_update()
        new_co = []
        for v in verts:
            edges = v.link_edges
            avg = Vector((0.0, 0.0, 0.0))
            for e in edges:
                avg += e.other_vert(v).co
            avg /= len(edges)
            delta = avg - v.co
            n = v.normal
            if v not in center:
                # Move only along the surface -> no shrinking.
                delta -= n * delta.dot(n)
            new_co.append(v.co + delta * factor)
        for v, co in zip(verts, new_co):
            v.co = co
        clamp_center()
        project()
        clamp_center()
    if iterations == 0:
        project()
        clamp_center()


def apply_shading(bm, mode, angle):
    if mode == 'KEEP':
        return
    smooth = mode != 'FLAT'
    for f in bm.faces:
        f.smooth = smooth
    if mode == 'AUTO':
        for e in bm.edges:
            e.smooth = not (e.calc_face_angle(0.0) > angle)
    else:
        for e in bm.edges:
            e.smooth = True


def edit_bmesh(obj, func, *args, **kwargs):
    """Run ``func(bm, ...)`` on the mesh of ``obj`` and write it back."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        result = func(bm, *args, **kwargs)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    return result


# ---------------------------------------------------------------------------
# Modifier helpers
# ---------------------------------------------------------------------------

def add_shrinkwrap(obj, target, s):
    m = obj.modifiers.new(PREFIX + "Shrinkwrap", 'SHRINKWRAP')
    m.target = target
    m.wrap_method = s.sw_method
    if s.sw_method != 'NEAREST_VERTEX':
        m.wrap_mode = s.sw_mode
    m.offset = s.sw_offset
    if s.sw_method == 'PROJECT':
        m.use_negative_direction = True
        m.use_positive_direction = True
    return m


def add_mirror(obj, axis, merge):
    m = obj.modifiers.new(PREFIX + "Mirror", 'MIRROR')
    for i in range(3):
        m.use_axis[i] = (i == axis)
    m.use_clip = True
    m.use_mirror_merge = True
    m.merge_threshold = max(merge, 1e-5)
    return m


def add_data_transfer(context, obj, source, s, transfer_uvs):
    """Add a Data Transfer modifier for the enabled data types. Returns it or None."""
    verts, loops = set(), set()
    if s.transfer_vgroups:
        verts.add('VGROUP_WEIGHTS')
    if s.transfer_colors:
        verts.add('COLOR_VERTEX')
        loops.add('COLOR_CORNER')
    if transfer_uvs:
        loops.add('UV')
    if s.transfer_normals:
        loops.add('CUSTOM_NORMAL')
    if not verts and not loops:
        return None

    m = obj.modifiers.new(PREFIX + "DataTransfer", 'DATA_TRANSFER')
    m.object = source
    m.use_object_transform = True
    if verts:
        m.use_vert_data = True
        m.data_types_verts = verts
        m.vert_mapping = 'POLYINTERP_NEAREST'
    if loops:
        m.use_loop_data = True
        m.data_types_loops = loops
        m.loop_mapping = 'POLYINTERP_NEAREST'
    for attr in ("layers_vgroup_select_src", "layers_uv_select_src",
                 "layers_vcol_vert_select_src", "layers_vcol_loop_select_src",
                 "layers_vcol_select_src"):
        if hasattr(m, attr):
            setattr(m, attr, 'ALL')
    for attr in ("layers_vgroup_select_dst", "layers_uv_select_dst",
                 "layers_vcol_vert_select_dst", "layers_vcol_loop_select_dst",
                 "layers_vcol_select_dst"):
        if hasattr(m, attr):
            setattr(m, attr, 'NAME')
    if s.transfer_normals and hasattr(obj.data, "use_auto_smooth"):  # Blender < 4.1
        obj.data.use_auto_smooth = True
    # Create the destination layers (UV maps, color attributes, vertex groups).
    with context.temp_override(**override(obj)):
        for dtype in sorted(verts | loops):
            if dtype == 'CUSTOM_NORMAL':
                continue
            try:
                bpy.ops.object.datalayout_transfer(
                    modifier=m.name, data_type=dtype,
                    layers_select_src='ALL', layers_select_dst='NAME')
            except (RuntimeError, TypeError):
                pass
    return m


# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------

def clamp_voxel_size(me, voxel_size, max_res, warnings, label="Voxel"):
    dim = mesh_bounds_max_dim(me)
    if dim > 0 and dim / voxel_size > max_res:
        new = dim / max_res
        warnings.append(f"{label} size raised from {voxel_size:.5f} to {new:.5f} "
                        f"(resolution limit {max_res})")
        return new
    return voxel_size


def voxel_remesh(context, obj, voxel_size, adaptivity=0.0, fix_poles=True,
                 preserve_volume=True, preserve_attributes=False):
    me = obj.data
    me.remesh_voxel_size = voxel_size
    me.remesh_voxel_adaptivity = adaptivity
    me.use_remesh_fix_poles = fix_poles
    me.use_remesh_preserve_volume = preserve_volume
    me.use_remesh_preserve_attributes = preserve_attributes
    with context.temp_override(**override(obj)):
        bpy.ops.object.voxel_remesh()
    if len(obj.data.polygons) == 0:
        raise RetopoError("Voxel remesh produced an empty mesh - the voxel size is too large "
                          "for this object")


def decimate_to(context, obj, target_tris):
    tris = mesh_tri_count(obj.data)
    if tris <= target_tris:
        return
    m = obj.modifiers.new(PREFIX + "PreDecimate", 'DECIMATE')
    m.ratio = max(1e-4, target_tris / tris)
    apply_all_modifiers(context, obj)


def run_quadriflow(context, obj, s, target, warnings, seed=None):
    me = obj.data
    native = s.use_symmetry and s.qf_native_symmetry
    axis = s.symmetry_axis if native else None
    me.use_mirror_x = axis == 'X'
    me.use_mirror_y = axis == 'Y'
    me.use_mirror_z = axis == 'Z'
    # QuadriFlow rejects meshes with edges shorter than 1e-4 units (absolute), which
    # breaks small objects and fine voxel output. Work at a normalized size instead.
    dim = mesh_bounds_max_dim(me)
    factor = QUADRIFLOW_SIZE / dim if dim > 0 else 1.0
    me.transform(Matrix.Scale(factor, 4))

    def collapse_tiny(bm):
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=QUADRIFLOW_SIZE * 1e-5)
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

    edit_bmesh(obj, collapse_tiny)
    try:
        _quadriflow_attempts(context, obj, s, target, warnings, seed)
    finally:
        obj.data.transform(Matrix.Scale(1.0 / factor, 4))
        obj.data.update()


def _quadriflow_attempts(context, obj, s, target, warnings, seed=None):
    base_seed = s.qf_seed if seed is None else seed
    error = "unknown error"
    for attempt in range(s.qf_retries + 1):
        try:
            with context.temp_override(**override(obj)):
                result = bpy.ops.object.quadriflow_remesh(
                    use_mesh_symmetry=s.use_symmetry and s.qf_native_symmetry,
                    use_preserve_sharp=s.qf_preserve_sharp,
                    use_preserve_boundary=s.qf_preserve_boundary,
                    preserve_attributes=s.qf_preserve_attributes,
                    smooth_normals=s.qf_smooth_normals,
                    mode='FACES', target_faces=target, seed=base_seed + attempt)
        except RuntimeError as exc:
            error = str(exc).strip().replace("Error: ", "")
            continue
        if 'FINISHED' in result and len(obj.data.polygons) > 0:
            if attempt:
                warnings.append(f"QuadriFlow succeeded with seed {base_seed + attempt}")
            return
        error = "no output"
    hint = "" if s.pre_voxel_cleanup else " Enable 'Voxel Cleanup' in Pre-processing."
    raise RetopoError(f"QuadriFlow failed: {error}.{hint}")


def run_decimate(context, obj, s, target):
    m = obj.modifiers.new(PREFIX + "Decimate", 'DECIMATE')
    if s.dc_mode == 'COLLAPSE':
        m.decimate_type = 'COLLAPSE'
        join = s.dc_tris_to_quads and not s.dc_triangulate
        want_tris = target * 2 if join else target
        m.ratio = min(1.0, max(1e-4, want_tris / max(1, mesh_tri_count(obj.data))))
        m.use_collapse_triangulate = s.dc_triangulate
        if s.use_symmetry:
            m.use_symmetry = True
            m.symmetry_axis = s.symmetry_axis
    elif s.dc_mode == 'UNSUBDIV':
        m.decimate_type = 'UNSUBDIV'
        m.iterations = s.dc_iterations
    else:
        m.decimate_type = 'DISSOLVE'
        m.angle_limit = s.dc_angle_limit
        m.delimit = s.dc_delimit
    apply_all_modifiers(context, obj)
    if s.dc_mode == 'COLLAPSE' and s.dc_tris_to_quads and not s.dc_triangulate:
        edit_bmesh(obj, tris_to_quads, s.post_quad_face_angle, s.post_quad_shape_angle)


def run_octree(context, obj, s, edge, warnings):
    m = obj.modifiers.new(PREFIX + "Remesh", 'REMESH')
    m.mode = s.oc_mode
    depth = s.oc_depth
    if s.oc_auto_depth:
        dim = mesh_bounds_max_dim(obj.data)
        raw = math.log2(max(dim / max(s.oc_scale * edge, 1e-9), 2.0))
        depth = int(round(raw))
        if depth > 10:
            warnings.append(f"Octree depth clamped from {depth} to 10")
        depth = min(max(depth, 1), 10)
    m.octree_depth = depth
    m.scale = s.oc_scale
    m.sharpness = s.oc_sharpness
    m.use_remove_disconnected = s.oc_remove_disconnected
    m.threshold = s.oc_threshold
    m.use_smooth_shade = False
    apply_all_modifiers(context, obj)
    if len(obj.data.polygons) == 0:
        raise RetopoError("Octree remesh produced an empty mesh - try a higher depth "
                          "or disable 'Remove Disconnected'")


# ---------------------------------------------------------------------------
# UVs & Multires
# ---------------------------------------------------------------------------

def unwrap(context, obj, s):
    me = obj.data
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    for seq in (me.vertices, me.edges, me.polygons):
        seq.foreach_set("select", [True] * len(seq))
    vl = context.view_layer
    prev_active = vl.objects.active
    vl.objects.active = obj
    try:
        with context.temp_override(**override(obj)):
            bpy.ops.object.mode_set(mode='EDIT')
        try:
            with context.temp_override(edit_object=obj, **override(obj)):
                if s.uv_mode == 'SMART':
                    bpy.ops.uv.smart_project(angle_limit=s.uv_angle_limit,
                                             island_margin=s.uv_island_margin)
                else:
                    bpy.ops.uv.lightmap_pack(PREF_CONTEXT='ALL_FACES',
                                             PREF_MARGIN_DIV=max(0.001, min(1.0, s.uv_island_margin * 10)))
        finally:
            with context.temp_override(**override(obj)):
                bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        vl.objects.active = prev_active


def bake_multires(context, obj, ref, s):
    """Add a Multires modifier and reshape it onto the reference surface."""
    levels = s.subdiv_levels
    tmp = bpy.data.objects.new(PREFIX + "multires_target", obj.data.copy())
    context.scene.collection.objects.link(tmp)
    tmp.matrix_world = obj.matrix_world
    try:
        sub = tmp.modifiers.new("sub", 'SUBSURF')
        sub.levels = levels
        sw = add_shrinkwrap(tmp, ref, s)
        if s.sw_method == 'NEAREST_VERTEX':
            sw.wrap_method = 'NEAREST_SURFACEPOINT'
        apply_all_modifiers(context, tmp)

        mr = obj.modifiers.new(PREFIX + "Multires", 'MULTIRES')
        with context.temp_override(**override(obj)):
            for _ in range(levels):
                bpy.ops.object.multires_subdivide(modifier=mr.name, mode='CATMULL_CLARK')
        with context.temp_override(**override(obj, tmp)):
            bpy.ops.object.multires_reshape(modifier=mr.name)
        mr.levels = levels
        mr.sculpt_levels = levels
        mr.render_levels = levels
    finally:
        remove_object(tmp)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

GENERATED_TAG = "auto_retopo_generated"


def generated_materials(objects):
    """Materials created by the add-on (baked game materials) used by ``objects``."""
    mats = set()
    for ob in objects:
        if ob.type == 'MESH':
            mats.update(m for m in ob.data.materials if m is not None and m.get(GENERATED_TAG))
    return mats


def purge_generated(materials):
    """Remove add-on generated materials (and their baked images) nobody uses any more."""
    for mat in list(materials):
        if mat.users:
            continue
        images = [n.image for n in mat.node_tree.nodes
                  if n.type == 'TEX_IMAGE' and n.image is not None] if mat.node_tree else []
        bpy.data.materials.remove(mat)
        for img in images:
            if img.get(GENERATED_TAG) and img.users <= (1 if img.use_fake_user else 0):
                bpy.data.images.remove(img)


def find_previous_results(original):
    return [o for o in bpy.data.objects if o is not original and o.auto_retopo_source is original]


def run_retopology(context, original, s, max_voxel_res=1024, progress=None):
    """Retopologize ``original`` using settings ``s``.

    Returns a dict ``{"object", "input_faces", "output_faces", "time", "warnings"}``.
    Raises :class:`RetopoError` on user-facing failures.
    """
    t0 = time.time()
    warnings = []

    def step(name):
        if progress:
            progress(name)

    if original.type != 'MESH':
        raise RetopoError(f"'{original.name}' is not a mesh")

    replace = s.output_mode == 'REPLACE'
    rebuild_mode = s.method == 'REBUILD'
    if rebuild_mode:
        from . import rebuild
    # In replace mode nothing may reference the original -> bake everything.
    # The game-ready rebuild always produces a self-contained, fully applied mesh.
    bake_sw = s.sw_apply or replace or rebuild_mode
    bake_dt = s.transfer_apply or replace or rebuild_mode
    bake_mirror = s.symmetry_apply or replace or s.subdiv_mode == 'MULTIRES' or rebuild_mode
    axis = AXIS_INDEX[s.symmetry_axis]

    # ---- 1. Reference mesh -------------------------------------------------
    step("Preparing input")
    dg = context.evaluated_depsgraph_get()
    # An original (non-evaluated) object gives the base mesh without modifiers.
    src = original.evaluated_get(dg) if s.apply_modifiers else original
    ref_me = bpy.data.meshes.new_from_object(src, preserve_all_data_layers=True, depsgraph=dg)
    ref_me.name = PREFIX + original.name + "_ref"

    loc, rot, scale = original.matrix_world.decompose()
    if s.use_world_scale:
        matrix = Matrix.LocRotScale(loc, rot, None)
        smat = Matrix.Diagonal(scale).to_4x4()
        ref_me.transform(smat)
        flip = smat.determinant() < 0
    else:
        matrix = original.matrix_world.copy()
        flip = False

    bm = bmesh.new()
    bm.from_mesh(ref_me)
    if flip:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    preprocess_bmesh(bm, s, warnings)
    if not bm.faces:
        bm.free()
        bpy.data.meshes.remove(ref_me)
        raise RetopoError(f"'{original.name}' has no faces to retopologize")
    input_faces = len(bm.faces)
    input_tris = bm_tri_count(bm)
    area = bm_area(bm)
    bm.to_mesh(ref_me)
    bm.free()

    target, edge = compute_density(s, input_faces, area)
    if rebuild_mode:
        target = rebuild.triangle_budget(s, input_tris, area)  # triangles
        edge = rebuild.edge_for_budget(target, area)

    ref = bpy.data.objects.new(PREFIX + original.name + "_ref", ref_me)
    context.scene.collection.objects.link(ref)
    ref.matrix_world = matrix
    copy_vertex_group_names(original, ref)

    collection = get_output_collection(context, original, s.output_collection)
    work = bpy.data.objects.new(original.name + s.name_suffix, ref_me.copy())
    work.data.name = work.name
    collection.objects.link(work)
    work.matrix_world = matrix
    copy_vertex_group_names(original, work)

    if s.method == 'DECIMATE' or s.projection_target == 'ORIGINAL':
        use_shell = False
    elif s.projection_target == 'SHELL':
        use_shell = True
    else:  # AUTO = hybrid
        use_shell = True
    proj = ref  # surface used for projection
    shell_voxel = None

    def make_shell(mesh):
        shell = bpy.data.objects.new(PREFIX + original.name + "_shell", mesh)
        context.scene.collection.objects.link(shell)
        shell.matrix_world = matrix
        return shell

    success = False
    try:
        # ---- 2. Remesh -----------------------------------------------------
        if use_shell or rebuild_mode or (s.method == 'QUADRIFLOW' and s.pre_voxel_cleanup):
            detail = s.gr_shell_detail if rebuild_mode else s.pre_voxel_detail
            shell_voxel = clamp_voxel_size(work.data, edge / detail,
                                           max_voxel_res, warnings, "Cleanup voxel")
        if s.method == 'QUADRIFLOW':
            if s.pre_voxel_cleanup:
                step("Voxel cleanup")
                voxel_remesh(context, work, shell_voxel, fix_poles=False, preserve_volume=False,
                             preserve_attributes=s.qf_preserve_attributes)
                if use_shell:
                    proj = make_shell(work.data.copy())
            if s.pre_decimate:
                step("Pre-decimating")
                decimate_to(context, work, s.pre_decimate_max_faces * 2)
            step("QuadriFlow remesh")
            run_quadriflow(context, work, s, target, warnings)
        elif s.method == 'VOXEL':
            step("Voxel remesh")
            vs = clamp_voxel_size(work.data, edge, max_voxel_res, warnings)
            voxel_remesh(context, work, vs, s.vx_adaptivity, s.vx_fix_poles,
                         s.vx_preserve_volume, s.vx_preserve_attributes)
        elif s.method == 'DECIMATE':
            step("Decimating")
            run_decimate(context, work, s, target)
        elif rebuild_mode:
            # Throw the original topology away: start from a watertight shell.
            step("Building watertight shell")
            voxel_remesh(context, work, shell_voxel, fix_poles=False, preserve_volume=False)
            if use_shell:
                proj = make_shell(work.data.copy())
            if s.pre_decimate:
                step("Pre-decimating")
                decimate_to(context, work, s.pre_decimate_max_faces * 2)
        elif s.method == 'OCTREE':
            if s.pre_decimate:
                step("Pre-decimating")
                decimate_to(context, work, s.pre_decimate_max_faces * 2)
            step("Octree remesh")
            run_octree(context, work, s, edge, warnings)
        else:
            raise RetopoError(f"Unknown method {s.method!r}")

        if use_shell and proj is ref:
            step("Building outer shell")
            proj = make_shell(ref_me.copy())
            voxel_remesh(context, proj, shell_voxel, fix_poles=False, preserve_volume=False)
        orig_bvh = bvh_from_mesh(ref_me)
        hybrid = use_shell and s.projection_target == 'AUTO'
        if not use_shell:
            project_fn = make_projector(orig_bvh)
        elif hybrid:
            project_fn = make_projector(bvh_from_mesh(proj.data), orig_bvh, shell_voxel * 1.5)
        else:
            project_fn = make_projector(bvh_from_mesh(proj.data))

        winner = None
        hi_me = proj.data if proj is not ref else ref_me
        sw_target = proj  # shrinkwrap / multires target
        if rebuild_mode:
            winner, n_candidates = rebuild.build_best_candidate(
                context, work, s, target, edge, project_fn, hi_me, ref_me,
                make_projector(orig_bvh), warnings, step)
            if winner['style'] == 'DIRECT':
                # Keep separate / intersecting parts where they are: no outer shell.
                project_fn = make_projector(orig_bvh)
                sw_target = ref
                hybrid = False

        # ---- 3. Cleanup, symmetry, relax ----------------------------------
        step("Cleaning topology")
        merge_eps = max(s.symmetry_merge, edge * 1e-3)

        def cleanup(bm):
            # Weld coincident vertices (e.g. QuadriFlow's symmetry seam is left unwelded).
            bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=edge * 1e-4)
            remove_duplicate_faces(bm)
            if s.post_dissolve_degenerate:
                bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=edge * 1e-3)
            if s.post_merge_distance > 0.0:
                bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=s.post_merge_distance)
            if s.post_tris_to_quads and not rebuild_mode \
                    and not (s.method == 'DECIMATE' and s.dc_triangulate):
                tris_to_quads(bm, s.post_quad_face_angle, s.post_quad_shape_angle)
            if s.use_symmetry:
                symmetry_cut(bm, axis, s.symmetry_side, merge_eps, edge)
            step("Relaxing")
            relax_bmesh(bm, project_fn, s.relax_iterations, s.relax_factor,
                        s.relax_lock_boundary, s.relax_reproject,
                        sym_axis=axis if s.use_symmetry else None, sym_eps=merge_eps)
            delete_loose(bm)
            if s.post_recalc_normals:
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

        edit_bmesh(work, cleanup)

        # ---- 4. Projection & mirror (baked parts) -------------------------
        if s.use_shrinkwrap and bake_sw:
            step("Shrinkwrap")
            add_shrinkwrap(work, sw_target, s)
            apply_all_modifiers(context, work)
            exact = hybrid and s.sw_mode == 'ON_SURFACE' and s.sw_offset == 0.0

            def after_shrinkwrap(bm):
                if exact:  # refine shell positions to the exact original surface
                    for v in bm.verts:
                        loc = project_fn(v.co)
                        if loc is not None:
                            v.co = loc
                if s.use_symmetry:
                    for v in bm.verts:
                        if abs(v.co[axis]) <= merge_eps * 4 and v.is_boundary:
                            v.co[axis] = 0.0
            edit_bmesh(work, after_shrinkwrap)
        if s.use_symmetry and bake_mirror:
            step("Mirroring")
            add_mirror(work, axis, merge_eps)
            apply_all_modifiers(context, work)

        def finalize(bm):
            if s.post_recalc_normals:
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
            if s.post_triangulate:
                bmesh.ops.triangulate(bm, faces=bm.faces[:],
                                      quad_method=s.post_triangulate_method, ngon_method='BEAUTY')
            apply_shading(bm, s.shading, s.auto_smooth_angle)

        if rebuild_mode:
            step("Applying game mesh rules")
            edit_bmesh(work, rebuild.finish_topology, s)
            if hasattr(work.data, "use_auto_smooth"):  # Blender < 4.1
                work.data.use_auto_smooth = True
                work.data.auto_smooth_angle = math.pi
        else:
            edit_bmesh(work, finalize)
            if s.shading != 'KEEP' and hasattr(work.data, "use_auto_smooth"):  # Blender < 4.1
                work.data.use_auto_smooth = s.shading == 'AUTO'
                work.data.auto_smooth_angle = math.pi

        # ---- 5. UVs and data transfer --------------------------------------
        if rebuild_mode:
            if s.gr_unwrap:
                step("Unwrapping & packing UVs")
                rebuild.game_unwrap(context, work, s, warnings)
            else:
                for uv in list(work.data.uv_layers):
                    work.data.uv_layers.remove(uv)
        elif s.uv_mode in {'SMART', 'LIGHTMAP'}:
            step("Unwrapping UVs")
            for uv in list(work.data.uv_layers):
                work.data.uv_layers.remove(uv)
            unwrap(context, work, s)
        elif s.uv_mode == 'NONE':
            for uv in list(work.data.uv_layers):
                work.data.uv_layers.remove(uv)

        transfer_uvs = s.uv_mode == 'TRANSFER' and not rebuild_mode
        if transfer_uvs and not ref_me.uv_layers:
            warnings.append(f"'{original.name}' has no UV maps to transfer")
            transfer_uvs = False
        dt_wanted = transfer_uvs or s.transfer_colors or s.transfer_vgroups or s.transfer_normals
        if dt_wanted and bake_dt:
            step("Transferring data")
            add_data_transfer(context, work, ref, s, transfer_uvs)
            apply_all_modifiers(context, work)

        if rebuild_mode and s.gr_shading == 'WEIGHTED':
            step("Weighted normals")
            rebuild.apply_weighted_normals(context, work, s)

        if s.copy_materials:
            work.data.materials.clear()
            for mat in ref_me.materials:
                work.data.materials.append(mat)
        else:
            work.data.materials.clear()

        # ---- 6. Output object ---------------------------------------------
        result = work
        if replace:
            step("Replacing original mesh")
            old_me = original.data
            old_name = old_me.name
            original.modifiers.clear()
            original.data = work.data
            original.matrix_world = matrix
            original.vertex_groups.clear()
            for vg in work.vertex_groups:
                original.vertex_groups.new(name=vg.name)
            remove_object(work)
            work = None
            if old_me.users == 0:
                bpy.data.meshes.remove(old_me)
            original.data.name = old_name
            result = original
        else:
            if s.replace_previous:
                previous = find_previous_results(original)
                old_mats = generated_materials(previous)
                for old in previous:
                    remove_object(old)
                purge_generated(old_mats)
                result.name = original.name + s.name_suffix
                result.data.name = result.name
            result.auto_retopo_source = original

        # ---- 6b. Game-ready extras -----------------------------------------
        report, images, lods = None, [], []
        if rebuild_mode:
            step("Measuring quality")
            diag = rebuild.bounds_diagonal(hi_me)
            report = rebuild.evaluate(result.data, orig_bvh, rebuild.surface_samples(hi_me),
                                      diag, s.gr_priority)
            report.update(style=winner['style'], seed=winner['seed'], candidates=n_candidates,
                          budget=target)
            if s.gr_bake_normal or s.gr_bake_ao:
                step("Baking texture maps")
                extrusion = s.gr_cage_extrusion or rebuild.auto_extrusion(report, diag)
                images = rebuild.bake_maps(context, result, ref, s, extrusion,
                                           rebuild._base_color_of(original), warnings)
            rebuild.apply_transform_options(result, s)
            if not replace:
                step("Generating LODs")
                lods = rebuild.make_lods(context, result, original, s, warnings)
            if report['tris'] > target * 1.2:
                warnings.append(f"{report['tris']:,} triangles is over the budget of {target:,}")
            if report['non_manifold']:
                warnings.append(f"{report['non_manifold']} non-manifold edges remain")

        # ---- 7. Multires / subdivision / live modifiers --------------------
        if s.subdiv_mode == 'MULTIRES' and not rebuild_mode:
            step("Baking detail into Multires")
            bake_multires(context, result, sw_target, s)

        live_target = original if not replace else None
        if live_target is not None:
            if s.use_shrinkwrap and not bake_sw:
                add_shrinkwrap(result, live_target, s)
            if s.use_symmetry and not bake_mirror:
                add_mirror(result, axis, merge_eps)
            if dt_wanted and not bake_dt:
                add_data_transfer(context, result, live_target, s, transfer_uvs)
        if s.subdiv_mode == 'SUBSURF' and not rebuild_mode:
            m = result.modifiers.new(PREFIX + "Subdivision", 'SUBSURF')
            m.levels = s.subdiv_levels
            m.render_levels = s.subdiv_render_levels

        # ---- 8. Display -----------------------------------------------------
        result.show_wire = s.show_wire
        result.show_in_front = s.show_in_front
        result.display_type = 'TEXTURED'
        if not replace:
            if s.original_display == 'HIDE':
                try:
                    original.hide_set(True)
                except RuntimeError:
                    original.hide_viewport = True
            elif s.original_display == 'WIRE':
                original.display_type = 'WIRE'
            elif s.original_display == 'BOUNDS':
                original.display_type = 'BOUNDS'

        success = True
        output_faces = len(result.data.polygons)
        return dict(object=result, input_faces=input_faces, output_faces=output_faces,
                    target_faces=target, time=time.time() - t0, warnings=warnings,
                    report=report, images=images, lods=lods)
    finally:
        if proj is not ref:
            remove_object(proj)
        remove_object(ref)
        if not success and work is not None:
            remove_object(work)
