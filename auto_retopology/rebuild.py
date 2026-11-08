# SPDX-License-Identifier: GPL-3.0-or-later
"""Game-Ready Rebuild.

Rebuilds the topology from scratch (nothing of the original edge flow is
kept) so the result has the *same shape* but production quality for games:

* Several candidate topologies are generated from a watertight voxel shell of
  the original (QuadriFlow with different seeds and/or a curvature adaptive
  QEM build), each is relaxed onto the surface and then scored on shape
  deviation (both directions, so lost silhouette counts) and topology quality
  (poles, folds, quad ratio). The best one wins.
* Game rules: triangle budget, no n-gons, no stray valence-2 vertices, hard
  edges by angle with UV seams split on them, smooth / weighted normals,
  optional triangulation, UV unwrap + pack with pixel padding.
* Normal map (and AO) baked from the original with an automatic cage, wired
  into a PBR material, LODs, origin / rotation clean-up and a quality report.

The entry points are called from :func:`core.run_retopology`.
"""

import math

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import core

# How strongly topology quality counts against shape accuracy.
PRIORITY_WEIGHT = {'SHAPE': 0.25, 'BALANCED': 1.0, 'TOPOLOGY': 4.0}
MAX_SAMPLES = 6000


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def triangle_budget(s, input_tris, area):
    """Triangle budget for the rebuild from the shared density settings."""
    area = max(area, 1e-12)
    if s.density_mode == 'FACES':
        tris = s.target_faces if s.gr_count_triangles else s.target_faces * 2
    elif s.density_mode == 'RATIO':
        tris = input_tris * s.target_ratio
    else:  # quads of the given edge length = 2 triangles each
        tris = 2.0 * area / (s.target_edge_length ** 2)
    return max(16, int(round(tris)))


def edge_for_budget(tri_budget, area):
    return math.sqrt(max(area, 1e-12) / max(tri_budget / 2.0, 1.0))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def surface_samples(me, limit=MAX_SAMPLES):
    """Evenly strided vertex positions of ``me`` (the high resolution surface)."""
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co.shape = (n, 3)
    step = max(1, n // limit)
    return [Vector(p) for p in co[::step]]


def bounds_diagonal(me):
    n = len(me.vertices)
    if n == 0:
        return 1.0
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co.shape = (n, 3)
    return float(np.linalg.norm(co.max(axis=0) - co.min(axis=0))) or 1.0


def evaluate(me, hi_bvh, hi_points, diag, priority='BALANCED'):
    """Score a candidate mesh. Lower is better.

    Shape error is measured both ways: original surface samples -> candidate
    (catches lost silhouette / volume) and candidate face centers + edge
    midpoints -> original (catches faces bulging or cutting corners).
    """
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        if not bm.faces:
            return dict(score=float('inf'))
        bm.normal_update()
        lo_bvh = BVHTree.FromBMesh(bm)
        back = []
        for p in hi_points:
            hit = lo_bvh.find_nearest(p)
            back.append(hit[3] if hit[0] is not None else diag)
        fwd = []
        for f in bm.faces:
            hit = hi_bvh.find_nearest(f.calc_center_median())
            fwd.append(hit[3] if hit[0] is not None else diag)
        for e in bm.edges:
            hit = hi_bvh.find_nearest((e.verts[0].co + e.verts[1].co) * 0.5)
            fwd.append(hit[3] if hit[0] is not None else diag)
        back = np.asarray(back)
        fwd = np.asarray(fwd)
        rms = math.sqrt((float((back ** 2).mean()) + float((fwd ** 2).mean())) * 0.5)
        dev_max = float(max(back.max(), fwd.max()))

        faces = len(bm.faces)
        quads = sum(1 for f in bm.faces if len(f.verts) == 4)
        ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
        interior = [v for v in bm.verts if v.link_faces and not v.is_boundary]
        poles = sum(1 for v in interior if len(v.link_edges) != 4)
        bad_poles = sum(1 for v in interior if len(v.link_edges) >= 6 or len(v.link_edges) <= 2)
        manifold = [e for e in bm.edges if e.is_manifold]
        folds = sum(1 for e in manifold if e.calc_face_angle(0.0) > math.radians(100.0))
        non_manifold = sum(1 for e in bm.edges if len(e.link_faces) > 2 or not e.link_faces)
        tris = sum(len(f.verts) - 2 for f in bm.faces)

        quad_ratio = quads / faces
        pole_frac = poles / max(1, len(interior))
        bad_pole_frac = bad_poles / max(1, len(interior))
        fold_frac = folds / max(1, len(manifold))

        shape = 100.0 * rms / diag + 25.0 * dev_max / diag
        topo = (2.0 * pole_frac + 6.0 * bad_pole_frac + 1.0 * (1.0 - quad_ratio)
                + 10.0 * fold_frac + 0.5 * ngons / faces)
        score = shape + PRIORITY_WEIGHT[priority] * topo + 5.0 * non_manifold / max(1, len(bm.edges))
        return dict(score=score, rms=rms, dev_max=dev_max, rms_pct=100.0 * rms / diag,
                    max_pct=100.0 * dev_max / diag, quad_ratio=quad_ratio, poles=poles,
                    bad_poles=bad_poles, folds=folds, ngons=ngons, faces=faces, tris=tris,
                    non_manifold=non_manifold)
    finally:
        bm.free()


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _clean_candidate(bm, edge, project_fn, relax_iterations, relax_factor, quads):
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=edge * 1e-4)
    core.remove_duplicate_faces(bm)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=edge * 1e-3)
    if quads:
        core.tris_to_quads(bm, math.radians(40.0), math.radians(40.0))
    core.delete_loose(bm)
    core.relax_bmesh(bm, project_fn, relax_iterations, relax_factor,
                     lock_boundary=True, reproject=True)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])


def is_closed_manifold(me):
    """True if every part of ``me`` is closed, edge-manifold and consistently oriented.

    Such meshes (including several closed parts that intersect, typical for
    hard-surface / kitbash assets) can be remeshed directly by QuadriFlow,
    which keeps sharp features crisp.
    """
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        return bool(bm.faces) and all(e.is_manifold and e.is_contiguous for e in bm.edges)
    finally:
        bm.free()


def build_best_candidate(context, work, s, tri_budget, edge, project_fn, shell_me, ref_me,
                         orig_project_fn, warnings, step):
    """Replace ``work``'s dense shell mesh with the best scoring rebuilt topology.

    Candidates are built from the watertight shell (``work``) and, when the
    original is closed and manifold, directly from the original surface.
    Returns ``(metrics_of_winner, number_of_candidates)``.
    """
    base = work.data.copy()
    hi_bvh = core.bvh_from_mesh(ref_me)     # forward error: the exact original surface
    hi_points = surface_samples(shell_me)   # silhouette coverage: the outer surface
    diag = bounds_diagonal(shell_me)
    relax_iter = max(1, min(s.relax_iterations, 3))

    plans = []
    if s.gr_style in {'QUADS', 'BEST'}:
        for i in range(max(1, s.gr_candidates)):
            plans.append(('QUADS', s.qf_seed + i * 7))
        if s.gr_try_direct and is_closed_manifold(ref_me):
            for i in range(max(1, (s.gr_candidates + 1) // 2)):
                plans.append(('DIRECT', s.qf_seed + i * 7))
    if s.gr_style in {'ADAPTIVE', 'BEST'}:
        plans.append(('ADAPTIVE', 0))

    best = None
    errors = []
    tmp_objects = []
    try:
        for idx, (style, seed) in enumerate(plans, 1):
            step(f"Candidate {idx}/{len(plans)} ({style.lower()})")
            source = ref_me if style == 'DIRECT' else base
            tmp = bpy.data.objects.new(core.PREFIX + "candidate", source.copy())
            context.scene.collection.objects.link(tmp)
            tmp.matrix_world = work.matrix_world
            tmp_objects.append(tmp)
            try:
                if style == 'DIRECT' and s.pre_decimate:
                    core.decimate_to(context, tmp, s.pre_decimate_max_faces * 2)
                if style in {'QUADS', 'DIRECT'}:
                    core.run_quadriflow(context, tmp, s, max(4, tri_budget // 2), warnings, seed=seed)
                else:
                    core.decimate_to(context, tmp, tri_budget)
            except core.RetopoError as exc:
                errors.append(str(exc))
                continue
            # Direct candidates may keep hidden parts (intersecting pieces), so they are
            # projected onto the original itself rather than onto the outer shell.
            proj = orig_project_fn if style == 'DIRECT' else project_fn
            core.edit_bmesh(tmp, _clean_candidate, edge, proj, relax_iter,
                            s.relax_factor, quads=(style != 'ADAPTIVE' or s.gr_adaptive_quads))
            metrics = evaluate(tmp.data, hi_bvh, hi_points, diag, s.gr_priority)
            metrics.update(style=style, seed=seed)
            if best is None or metrics['score'] < best[1]['score']:
                best = (tmp, metrics)
        if best is None:
            raise core.RetopoError("Every rebuild candidate failed: " + "; ".join(errors[:2]))
        winner, metrics = best
        old = work.data
        work.data = winner.data.copy()
        work.data.name = old.name
        bpy.data.meshes.remove(old)
        return metrics, len(plans)
    finally:
        for tmp in tmp_objects:
            core.remove_object(tmp)
        if base.users == 0:
            bpy.data.meshes.remove(base)


# ---------------------------------------------------------------------------
# Game topology / shading / UVs
# ---------------------------------------------------------------------------

def finish_topology(bm, s):
    """Enforce game mesh rules and mark hard edges / seams."""
    # No n-gons.
    ngons = [f for f in bm.faces if len(f.verts) > 4]
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons, quad_method='BEAUTY', ngon_method='BEAUTY')
    # No interior valence-2 vertices (they add nothing but shading artifacts).
    lonely = [v for v in bm.verts
              if len(v.link_edges) == 2 and not v.is_boundary and len(v.link_faces) == 2]
    if lonely:
        bmesh.ops.dissolve_verts(bm, verts=lonely)
    core.delete_loose(bm)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])

    if s.gr_triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:],
                              quad_method=s.post_triangulate_method, ngon_method='BEAUTY')

    mark_hard_edges(bm, s)


def mark_hard_edges(bm, s):
    hard_angle = s.gr_hard_angle
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        hard = s.gr_shading != 'SMOOTH' and e.calc_face_angle(0.0) > hard_angle
        e.smooth = not hard
        if s.gr_seams_at_hard:
            e.seam = hard


def game_unwrap(context, obj, s, warnings):
    """Smart project (split at hard edges) + uniform texel density + padded pack."""
    me = obj.data
    for uv in list(me.uv_layers):
        me.uv_layers.remove(uv)
    me.uv_layers.new(name="UVMap")
    for seq in (me.vertices, me.edges, me.polygons):
        seq.foreach_set("select", [True] * len(seq))
    size = int(s.gr_texture_size)
    margin = s.gr_padding / size
    angle = min(s.uv_angle_limit, s.gr_hard_angle) if s.gr_seams_at_hard else s.uv_angle_limit
    vl = context.view_layer
    prev = vl.objects.active
    vl.objects.active = obj
    try:
        with context.temp_override(**core.override(obj)):
            bpy.ops.object.mode_set(mode='EDIT')
        try:
            with context.temp_override(edit_object=obj, **core.override(obj)):
                bpy.ops.uv.smart_project(angle_limit=max(angle, math.radians(1.0)),
                                         island_margin=0.0)
                bpy.ops.uv.average_islands_scale()
                try:
                    bpy.ops.uv.pack_islands(margin=margin, rotate=True, margin_method='FRACTION')
                except TypeError:
                    bpy.ops.uv.pack_islands(margin=margin, rotate=True)
                # Make the island borders visible as seams (keeps hard-edge seams too).
                bpy.ops.uv.seams_from_islands(mark_seams=True, mark_sharp=False)
        finally:
            with context.temp_override(**core.override(obj)):
                bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        vl.objects.active = prev


def apply_weighted_normals(context, obj, s):
    m = obj.modifiers.new(core.PREFIX + "WeightedNormal", 'WEIGHTED_NORMAL')
    m.mode = 'FACE_AREA'
    m.weight = 50
    m.keep_sharp = True
    if hasattr(obj.data, "use_auto_smooth"):  # Blender < 4.1
        obj.data.use_auto_smooth = True
    core.apply_all_modifiers(context, obj)


# ---------------------------------------------------------------------------
# Baking
# ---------------------------------------------------------------------------

def _base_color_of(original):
    for slot in getattr(original, "material_slots", []):
        mat = slot.material
        if mat and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == 'BSDF_PRINCIPLED':
                    return tuple(node.inputs['Base Color'].default_value)
            break
    return (0.8, 0.8, 0.8, 1.0)


def make_game_material(name, base_color, want_normal, want_ao):
    mat = bpy.data.materials.new(name)
    mat[core.GENERATED_TAG] = True
    nt = mat.node_tree
    if nt is None:  # pragma: no cover - Blender < 5 creates the tree on demand
        mat.use_nodes = True
        nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Base Color'].default_value = base_color
    nodes = {}
    links = []  # connected after baking, so no map is read while it is baked into
    if want_normal:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.label = "Normal Map"
        tex.location = (-700, -250)
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (-350, -250)
        links.append((tex.outputs['Color'], nmap.inputs['Color']))
        links.append((nmap.outputs['Normal'], bsdf.inputs['Normal']))
        nodes['NORMAL'] = tex
    if want_ao:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.label = "Ambient Occlusion"
        tex.location = (-700, 200)
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.location = (-350, 200)
        mix.inputs['Factor'].default_value = 1.0
        a = next(sk for sk in mix.inputs if sk.name == 'A' and sk.type == 'RGBA')
        b = next(sk for sk in mix.inputs if sk.name == 'B' and sk.type == 'RGBA')
        a.default_value = base_color
        links.append((tex.outputs['Color'], b))
        out = next(sk for sk in mix.outputs if sk.type == 'RGBA')
        links.append((out, bsdf.inputs['Base Color']))
        nodes['AO'] = tex
    return mat, nodes, links


def _store_image(img, directory, warnings):
    if directory:
        folder = bpy.path.abspath(directory)
        try:
            import os
            os.makedirs(folder, exist_ok=True)
            img.filepath_raw = os.path.join(folder, bpy.path.clean_name(img.name) + ".png")
            img.file_format = 'PNG'
            img.save()
            return
        except (OSError, RuntimeError) as exc:
            warnings.append(f"Could not save {img.name} to '{directory}' ({exc}); packed instead")
    try:
        img.pack()
    except RuntimeError:
        pass
    img.use_fake_user = True


def bake_maps(context, low, high, s, extrusion, base_color, warnings):
    """Bake normal / AO maps from ``high`` onto ``low`` and assign a game material."""
    want_normal, want_ao = s.gr_bake_normal, s.gr_bake_ao
    if not (want_normal or want_ao):
        return []
    if not low.data.uv_layers:
        warnings.append("Baking skipped: the result has no UVs (enable Unwrap UVs)")
        return []
    size = int(s.gr_texture_size)
    mat, nodes, links = make_game_material(low.name + "_Game", base_color, want_normal, want_ao)
    old_mats = core.generated_materials([low])
    low.data.materials.clear()
    low.data.materials.append(mat)
    core.purge_generated(old_mats)
    low.data.polygons.foreach_set("material_index", [0] * len(low.data.polygons))

    scene = context.scene
    saved_engine = scene.render.engine
    scene.render.engine = 'CYCLES'
    saved_samples = scene.cycles.samples
    images = []
    try:
        for kind in ('NORMAL', 'AO'):
            if kind not in nodes:
                continue
            img = bpy.data.images.new(f"{low.name}_{kind.lower()}", size, size, alpha=False)
            img[core.GENERATED_TAG] = True
            if kind == 'NORMAL':
                img.colorspace_settings.name = 'Non-Color'
            node = nodes[kind]
            node.image = img
            mat.node_tree.nodes.active = node
            scene.cycles.samples = 1 if kind == 'NORMAL' else s.gr_ao_samples
            kwargs = dict(type=kind, use_selected_to_active=True, cage_extrusion=extrusion,
                          max_ray_distance=extrusion * 2.0, margin=s.gr_padding,
                          use_clear=True)
            if kind == 'NORMAL':
                kwargs['normal_space'] = 'TANGENT'
            with context.temp_override(object=low, active_object=low,
                                       selected_objects=[low, high],
                                       selected_editable_objects=[low, high]):
                bpy.ops.object.bake(**kwargs)
            _store_image(img, s.gr_bake_dir, warnings)
            images.append(img)
    except RuntimeError as exc:
        warnings.append(f"Baking failed: {str(exc).strip()}")
    finally:
        scene.cycles.samples = saved_samples
        scene.render.engine = saved_engine
        for src, dst in links:
            mat.node_tree.links.new(src, dst)
    return images


# ---------------------------------------------------------------------------
# Transforms & LODs
# ---------------------------------------------------------------------------

def apply_transform_options(obj, s):
    if s.gr_apply_rotation:
        loc, rot, scale = obj.matrix_world.decompose()
        obj.data.transform(rot.to_matrix().to_4x4() @ Matrix.Diagonal(scale).to_4x4())
        obj.matrix_world = Matrix.Translation(loc)
    if s.gr_origin != 'KEEP':
        me = obj.data
        n = len(me.vertices)
        co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co.shape = (n, 3)
        lo, hi = co.min(axis=0), co.max(axis=0)
        center = (lo + hi) * 0.5
        if s.gr_origin == 'BOTTOM':
            center[2] = lo[2]
        offset = Vector(center)
        me.transform(Matrix.Translation(-offset))
        obj.matrix_world = obj.matrix_world @ Matrix.Translation(offset)
    obj.data.update()


def _fix_lod(bm, s):
    core.delete_loose(bm)
    mark_hard_edges(bm, s)


def make_lods(context, result, original, s, warnings):
    lods = []
    if s.gr_lod_count <= 0:
        return lods
    width = max(result.dimensions.x, 1e-3)
    collections = list(result.users_collection) or [context.scene.collection]
    for i in range(1, s.gr_lod_count + 1):
        me = result.data.copy()
        ob = bpy.data.objects.new(f"{result.name}_LOD{i}", me)
        me.name = ob.name
        for coll in collections:
            coll.objects.link(ob)
        ob.matrix_world = result.matrix_world
        dec = ob.modifiers.new(core.PREFIX + "LOD", 'DECIMATE')
        dec.ratio = max(0.01, s.gr_lod_ratio ** i)
        core.apply_all_modifiers(context, ob)
        core.edit_bmesh(ob, _fix_lod, s)
        if s.gr_shading == 'WEIGHTED':
            apply_weighted_normals(context, ob, s)
        if s.gr_lod_spread:
            ob.location.x += width * 1.25 * i
        ob.auto_retopo_source = original
        ob.show_wire = result.show_wire
        lods.append(ob)
        if len(ob.data.polygons) < 4:
            warnings.append(f"LOD{i} has almost no faces - lower the LOD count or raise the ratio")
    return lods


def auto_extrusion(metrics, diag):
    """Cage extrusion that comfortably covers the measured deviation."""
    return max(metrics.get('dev_max', 0.0) * 1.5, diag * 0.004)
