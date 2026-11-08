# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless test-suite for the Auto Retopology add-on.

Run with either::

    blender --background --factory-startup --python tests/test_addon.py
    python tests/test_addon.py            # with the `bpy` module from PyPI installed
"""

import math
import os
import sys
import traceback

import bpy  # must come first when using the PyPI `bpy` module
import bmesh
from mathutils.kdtree import KDTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import auto_retopology  # noqa: E402
from auto_retopology import presets, ui  # noqa: E402

RESULTS = []


def case(func):
    RESULTS.append(func)
    return func


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def reset_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)
    presets.reset_settings(bpy.context.scene.auto_retopo)
    bpy.context.scene.auto_retopo_stats.object_name = ""


def make_object(kind="monkey", name=None, location=(0, 0, 0), scale=(1, 1, 1)):
    me = bpy.data.meshes.new(name or kind)
    bm = bmesh.new()
    if kind == "monkey":
        bmesh.ops.create_monkey(bm)
    elif kind == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=2.0)
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=6, use_grid_fill=True)
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=20, y_segments=20, size=1.0)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name or kind, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = location
    ob.scale = scale
    select_only(ob)
    return ob


def select_only(ob):
    bpy.context.view_layer.update()
    for o in bpy.context.view_layer.objects:
        if o is not None:
            o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob


def settings():
    return bpy.context.scene.auto_retopo


def run_op():
    res = bpy.ops.auto_retopo.run()
    assert res == {'FINISHED'}, res
    return bpy.context.view_layer.objects.active


def face_sizes(ob):
    return [len(p.vertices) for p in ob.data.polygons]


def quad_ratio(ob):
    sizes = face_sizes(ob)
    return sum(1 for n in sizes if n == 4) / max(1, len(sizes))


def assert_symmetric(ob, axis=0, tol=1e-3):
    me = ob.data
    kd = KDTree(len(me.vertices))
    for i, v in enumerate(me.vertices):
        kd.insert(v.co, i)
    kd.balance()
    worst = 0.0
    for v in me.vertices:
        co = v.co.copy()
        co[axis] = -co[axis]
        worst = max(worst, kd.find(co)[2])
    assert worst < tol, f"not symmetric, worst mismatch {worst}"


def max_surface_distance(ob, src):
    """Largest distance of the result's vertices from the source surface (world space)."""
    from mathutils.bvhtree import BVHTree
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(src, dg)
    inv = src.matrix_world.inverted()
    worst = 0.0
    for v in ob.data.vertices:
        p = inv @ (ob.matrix_world @ v.co)
        hit = bvh.find_nearest(p)
        if hit[0] is not None:
            worst = max(worst, (src.matrix_world @ hit[0] - src.matrix_world @ p).length)
    return worst


# ---------------------------------------------------------------------------
# Fake UI layout: validates every prop / operator / menu reference.
# ---------------------------------------------------------------------------

class FakeLayout:
    def __init__(self, log):
        self.log = log

    def __getattr__(self, name):
        if name in {"active", "enabled", "scale_y", "scale_x", "use_property_split",
                    "use_property_decorate", "alignment"}:
            return True
        return lambda *a, **k: self

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def _sub(self, *a, **k):
        return self

    row = column = box = split = grid_flow = column_flow = _sub

    def prop(self, data, prop, **kw):
        assert prop in data.bl_rna.properties, f"missing property {prop!r} on {data}"
        self.log.append(("prop", prop))
        return self

    def prop_search(self, data, prop, search_data, search_prop, **kw):
        assert prop in data.bl_rna.properties, prop
        assert hasattr(search_data, search_prop)
        self.log.append(("prop", prop))
        return self

    def operator(self, idname, **kw):
        mod, op = idname.split(".")
        assert hasattr(getattr(bpy.ops, mod), op), f"missing operator {idname}"
        bpy.ops._op_as_string = None
        self.log.append(("op", idname))

        class Props:
            def __setattr__(s, k, v):
                pass
        return Props()

    def menu(self, idname, **kw):
        assert hasattr(bpy.types, idname), f"missing menu {idname}"
        return self

    def label(self, **kw):
        return self

    def separator(self, **kw):
        return self


class FakePanel:
    def __init__(self, log):
        self.layout = FakeLayout(log)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@case
def test_quadriflow_monkey():
    reset_scene()
    src = make_object("monkey")
    settings().target_faces = 1500
    res = run_op()
    assert res is not src and res.auto_retopo_source is src
    n = len(res.data.polygons)
    assert 700 < n < 3000, n
    assert quad_ratio(res) > 0.9, quad_ratio(res)
    assert src.hide_get(), "original should be hidden"
    assert res.name == "monkey_retopo"
    assert max_surface_distance(res, src) < 0.02


@case
def test_voxel_edge_length_scaled_object():
    reset_scene()
    src = make_object("sphere", scale=(2, 2, 2))
    s = settings()
    s.method = 'VOXEL'
    s.density_mode = 'EDGE_LENGTH'
    s.target_edge_length = 0.2
    res = run_op()
    # Sphere radius 2 -> area ~50.3 -> ~1250 faces at 0.2 edge length
    n = len(res.data.polygons)
    assert 500 < n < 3000, n
    assert tuple(round(c, 4) for c in res.scale) == (1, 1, 1), res.scale
    assert abs(res.dimensions.x - 4.0) < 0.1, res.dimensions


@case
def test_decimate_ratio_keeps_uvs():
    reset_scene()
    src = make_object("sphere")
    src.data.uv_layers.new(name="UVMap")
    s = settings()
    s.method = 'DECIMATE'
    s.density_mode = 'RATIO'
    s.target_ratio = 0.25
    s.uv_mode = 'TRANSFER'
    s.relax_iterations = 0
    res = run_op()
    n_in = len(src.data.polygons)
    n = len(res.data.polygons)
    assert 0.1 * n_in < n < 0.5 * n_in, (n_in, n)
    assert len(res.data.uv_layers) == 1


@case
def test_decimate_modes():
    for mode in ('UNSUBDIV', 'DISSOLVE'):
        reset_scene()
        make_object("cube")
        s = settings()
        s.method = 'DECIMATE'
        s.dc_mode = mode
        res = run_op()
        assert len(res.data.polygons) > 0, mode


@case
def test_octree_modes():
    for mode in ('BLOCKS', 'SMOOTH', 'SHARP'):
        reset_scene()
        make_object("monkey")
        s = settings()
        s.method = 'OCTREE'
        s.oc_mode = mode
        s.target_faces = 2000
        res = run_op()
        assert len(res.data.polygons) > 100, mode


@case
def test_symmetry_applied():
    reset_scene()
    src = make_object("monkey")
    s = settings()
    s.use_symmetry = True
    s.target_faces = 1500
    res = run_op()
    assert not res.modifiers
    assert_symmetric(res, 0)
    assert quad_ratio(res) > 0.85


@case
def test_symmetry_seam_is_clean():
    """Mirrored seam must be welded, manifold and free of creases."""
    for kw in (dict(), dict(qf_native_symmetry=True), dict(method='VOXEL')):
        reset_scene()
        bpy.ops.mesh.primitive_torus_add()
        src = bpy.context.active_object
        src.modifiers.new("Sub", 'SUBSURF').levels = 2
        src.rotation_euler = (1.2, 0, 0)
        select_only(src)
        s = settings()
        s.use_symmetry = True
        s.target_faces = 800
        for k, v in kw.items():
            setattr(s, k, v)
        res = run_op()
        bm = bmesh.new()
        bm.from_mesh(res.data)
        non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
        worst = max(math.degrees(e.calc_face_angle(0)) for e in bm.edges if e.is_manifold)
        bm.free()
        assert non_manifold == 0, (kw, non_manifold)
        assert worst < 45, (kw, worst)
        assert_symmetric(res, 0)


@case
def test_symmetry_live_negative_side_y_axis():
    reset_scene()
    make_object("sphere")
    s = settings()
    s.use_symmetry = True
    s.symmetry_axis = 'Y'
    s.symmetry_side = 'NEGATIVE'
    s.symmetry_apply = False
    res = run_op()
    assert [m.type for m in res.modifiers] == ['MIRROR']
    assert max(v.co.y for v in res.data.vertices) <= 1e-6
    assert res.modifiers[0].use_axis[1] and not res.modifiers[0].use_axis[0]


@case
def test_live_modifier_stack():
    reset_scene()
    src = make_object("monkey")
    src.vertex_groups.new(name="Group")
    s = settings()
    s.target_faces = 1000
    s.sw_apply = False
    s.use_symmetry = True
    s.symmetry_apply = False
    s.transfer_vgroups = True
    s.transfer_apply = False
    s.subdiv_mode = 'SUBSURF'
    res = run_op()
    types = [m.type for m in res.modifiers]
    assert types == ['SHRINKWRAP', 'MIRROR', 'DATA_TRANSFER', 'SUBSURF'], types
    assert res.modifiers[0].target is src
    # Applying via the add-on operator keeps subsurf only.
    select_only(res)
    assert bpy.ops.auto_retopo.apply_modifiers() == {'FINISHED'}
    assert [m.type for m in res.modifiers] == ['SUBSURF']
    assert "Group" in res.vertex_groups


@case
def test_multires_bake():
    reset_scene()
    src = make_object("sphere")
    s = settings()
    s.target_faces = 400
    s.subdiv_mode = 'MULTIRES'
    s.subdiv_levels = 2
    res = run_op()
    assert res.modifiers and res.modifiers[0].type == 'MULTIRES'
    assert res.modifiers[0].total_levels == 2


@case
def test_uv_unwrap_modes():
    for mode in ('SMART', 'LIGHTMAP'):
        reset_scene()
        make_object("monkey")
        s = settings()
        s.target_faces = 800
        s.uv_mode = mode
        res = run_op()
        assert len(res.data.uv_layers) == 1, mode
        uvs = [d.uv.copy() for d in res.data.uv_layers[0].data]
        assert any(uv.length > 0 for uv in uvs), mode


@case
def test_data_transfer_baked():
    reset_scene()
    src = make_object("sphere")
    attr = src.data.color_attributes.new("Col", 'FLOAT_COLOR', 'POINT')
    for d in attr.data:
        d.color = (1, 0, 0, 1)
    vg = src.vertex_groups.new(name="Weights")
    vg.add(list(range(len(src.data.vertices))), 1.0, 'REPLACE')
    s = settings()
    s.target_faces = 600
    s.transfer_colors = True
    s.transfer_vgroups = True
    s.transfer_normals = True
    res = run_op()
    assert not res.modifiers
    assert "Weights" in res.vertex_groups
    assert len(res.data.color_attributes) >= 1
    assert res.data.has_custom_normals


@case
def test_replace_mode():
    reset_scene()
    src = make_object("monkey", scale=(1.5, 1.5, 1.5))
    src.modifiers.new("Sub", 'SUBSURF')
    s = settings()
    s.output_mode = 'REPLACE'
    s.target_faces = 1000
    res = run_op()
    assert res is src
    assert not src.modifiers
    assert 500 < len(src.data.polygons) < 2000
    assert len([o for o in bpy.data.objects]) == 1, [o.name for o in bpy.data.objects]
    assert tuple(round(c, 4) for c in src.scale) == (1, 1, 1)


@case
def test_rerun_replaces_previous():
    reset_scene()
    src = make_object("monkey")
    settings().target_faces = 800
    first = run_op()
    first_name = first.name
    select_only(first) if first.name in bpy.context.view_layer.objects else None
    settings().target_faces = 1600
    second = run_op()
    assert second.auto_retopo_source is src
    names = [o.name for o in bpy.data.objects]
    assert names.count(first_name) == 1 and len(names) == 2, names
    assert len(second.data.polygons) > 1100


@case
def test_batch_multiple_objects():
    reset_scene()
    a = make_object("monkey", name="A", location=(-3, 0, 0))
    b = make_object("sphere", name="B", location=(3, 0, 0))
    a.select_set(True)
    b.select_set(True)
    s = settings()
    s.target_faces = 600
    s.output_collection = "Retopo Results"
    run_op()
    coll = bpy.data.collections["Retopo Results"]
    assert sorted(o.name for o in coll.objects) == ["A_retopo", "B_retopo"]
    assert all(o.select_get() for o in coll.objects)


@case
def test_preprocessing_options():
    reset_scene()
    src = make_object("monkey")
    s = settings()
    s.pre_fill_holes = True
    s.pre_remove_small_parts = True
    s.pre_small_part_faces = 50
    s.pre_smooth_iterations = 3
    s.post_triangulate = True
    s.shading = 'FLAT'
    s.target_faces = 800
    res = run_op()
    assert set(face_sizes(res)) == {3}
    assert not any(p.use_smooth for p in res.data.polygons)


@case
def test_open_plane_quadriflow_without_cleanup():
    reset_scene()
    make_object("plane")
    s = settings()
    s.pre_voxel_cleanup = False
    s.qf_preserve_boundary = True
    s.target_faces = 200
    res = run_op()
    assert len(res.data.polygons) > 50


@case
def test_non_manifold_failure_reports_error():
    reset_scene()
    me = bpy.data.meshes.new("broken")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [(0, 1)], [])
    ob = bpy.data.objects.new("broken", me)
    bpy.context.scene.collection.objects.link(ob)
    select_only(ob)
    try:
        bpy.ops.auto_retopo.run()
    except RuntimeError as exc:
        assert "no faces" in str(exc), exc
    else:
        raise AssertionError("expected an error report")
    assert [o.name for o in bpy.data.objects] == ["broken"], "temp objects leaked"


@case
def test_all_quick_presets_run():
    for key in presets.QUICK_PRESETS:
        reset_scene()
        make_object("monkey")
        assert bpy.ops.auto_retopo.quick_preset(preset=key) == {'FINISHED'}
        s = settings()
        if s.density_mode == 'FACES':
            s.target_faces = min(s.target_faces, 1500)
        if s.subdiv_mode == 'MULTIRES':
            s.subdiv_levels = 1
        if s.method == 'REBUILD':  # keep the suite fast
            s.gr_candidates = 1
            s.gr_texture_size = '256'
            s.gr_ao_samples = 4
        res = run_op()
        assert len(res.data.polygons) > 0, key


# ---------------------------------------------------------------------------
# Game-Ready Rebuild
# ---------------------------------------------------------------------------

def make_blob(name="blob"):
    from mathutils import noise
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=5, radius=1.0)
    for v in bm.verts:
        n = v.co.normalized()
        v.co = n * (1.0 + 0.25 * noise.noise(n * 2.0) + 0.05 * noise.noise(n * 7.0))
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    select_only(ob)
    return ob


def rebuild_settings(**kw):
    s = settings()
    s.method = 'REBUILD'
    s.gr_texture_size = '256'
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def tri_count(ob):
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


@case
def test_rebuild_game_ready_defaults():
    reset_scene()
    src = make_blob()
    rebuild_settings(target_faces=2000)
    res = run_op()
    tris = tri_count(res)
    assert 1600 <= tris <= 2400, tris
    assert all(len(p.vertices) <= 4 for p in res.data.polygons), "n-gons left"
    assert quad_ratio(res) > 0.9
    st = bpy.context.scene.auto_retopo_stats
    assert st.has_report and st.object_name == res.name
    assert st.non_manifold == 0 and st.boundary == 0
    assert st.dev_avg < 1.0, st.dev_avg
    # 3 shell candidates + 2 direct ones (the blob is closed and manifold)
    assert st.winner.startswith("Clean Quads") and "best of 5" in st.winner, st.winner
    # UVs, seams, baked + packed normal map wired into a game material
    assert len(res.data.uv_layers) == 1
    mat = res.data.materials[0]
    assert mat.get("auto_retopo_generated")
    tex = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE']
    assert len(tex) == 1 and tex[0].image.size[0] == 256
    assert tex[0].image.packed_file is not None
    assert any(link.to_node.type == 'NORMAL_MAP' for link in mat.node_tree.links)
    # LODs: two levels, decreasing triangle counts, linked to the source
    lods = sorted((o for o in bpy.data.objects if "_LOD" in o.name), key=lambda o: o.name)
    assert [o.name for o in lods] == [res.name + "_LOD1", res.name + "_LOD2"]
    assert tris > tri_count(lods[0]) > tri_count(lods[1]) > 0
    assert all(o.auto_retopo_source is src for o in lods)
    assert max_surface_distance(res, src) < 0.05


@case
def test_rebuild_styles():
    # BEST with 2 candidates: 2 shell quads + 1 direct quads + 1 adaptive
    for style, cands in (('ADAPTIVE', "best of 1"), ('BEST', "best of 4")):
        reset_scene()
        make_blob()
        rebuild_settings(target_faces=1500, gr_style=style, gr_candidates=2,
                         gr_bake_normal=False, gr_lod_count=0)
        res = run_op()
        st = bpy.context.scene.auto_retopo_stats
        assert cands in st.winner, (style, st.winner)
        tris = tri_count(res)
        assert 1200 <= tris <= 1800, (style, tris)
        assert st.non_manifold == 0


@case
def test_rebuild_direct_keeps_intersecting_parts_crisp():
    """Closed, intersecting parts can win as a direct remesh and stay separate parts."""
    reset_scene()
    me = bpy.data.meshes.new("kit")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.6)
    cyl = bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=0.45, radius2=0.45,
                                depth=0.5)
    bmesh.ops.translate(bm, verts=cyl['verts'], vec=(0, 0, 0.95))
    bm.to_mesh(me)
    bm.free()
    src = bpy.data.objects.new("kit", me)
    bpy.context.scene.collection.objects.link(src)
    select_only(src)
    rebuild_settings(target_faces=1500, gr_candidates=1, qf_preserve_sharp=True,
                     gr_priority='SHAPE', gr_bake_normal=False, gr_lod_count=0)
    s = settings()
    s.gr_try_direct = False
    run_op()
    shell_dev = bpy.context.scene.auto_retopo_stats.dev_avg
    s.gr_try_direct = True
    run_op()
    st = bpy.context.scene.auto_retopo_stats
    assert "direct" in st.winner, st.winner
    assert st.islands == 2, st.islands  # box and cylinder stay separate parts
    assert st.dev_avg < shell_dev, (st.dev_avg, shell_dev)


@case
def test_rebuild_rerun_purges_old_bakes():
    reset_scene()
    make_blob()
    rebuild_settings(target_faces=800, gr_candidates=1, gr_bake_ao=True, gr_ao_samples=2)
    run_op()
    counts = (len(bpy.data.images), len(bpy.data.materials))
    run_op()  # re-run from the selected result
    assert (len(bpy.data.images), len(bpy.data.materials)) == counts, counts
    assert len([o for o in bpy.data.objects]) == 4  # source, result, 2 LODs


@case
def test_rebuild_symmetry_triangulate_origin():
    reset_scene()
    bpy.ops.mesh.primitive_torus_add()
    src = bpy.context.active_object
    src.modifiers.new("Sub", 'SUBSURF').levels = 2
    src.rotation_euler = (0.0, 0.0, 0.4)
    select_only(src)
    rebuild_settings(target_faces=1200, gr_candidates=1, use_symmetry=True,
                     gr_triangulate=True, gr_origin='BOTTOM', gr_apply_rotation=True,
                     gr_bake_normal=False, gr_lod_count=1)
    res = run_op()
    assert set(face_sizes(res)) == {3}
    assert tuple(round(c, 5) for c in res.rotation_euler) == (0, 0, 0)
    assert abs(min(v.co.z for v in res.data.vertices)) < 1e-4
    assert abs(res.location.z - (-0.25)) < 0.03, res.location
    # Symmetry is in the original's local space; check it in that frame.
    inv = src.matrix_world.inverted()
    tmp = res.data.copy()
    tmp.transform(inv @ res.matrix_world)
    holder = bpy.data.objects.new("sym_check", tmp)
    assert_symmetric(holder, 0, tol=2e-3)


@case
def test_rebuild_hard_surface_weighted():
    reset_scene()
    src = make_object("cube")
    import math as _m
    rebuild_settings(target_faces=1200, gr_candidates=1, qf_preserve_sharp=True,
                     gr_shading='WEIGHTED', gr_hard_angle=_m.radians(50), gr_lod_count=0)
    res = run_op()
    assert res.data.has_custom_normals
    sharp = sum(e.use_edge_sharp for e in res.data.edges)
    seams = sum(e.use_seam for e in res.data.edges)
    assert sharp >= 12 and seams >= sharp, (sharp, seams)


@case
def test_rebuild_bake_to_folder_and_replace():
    import tempfile
    reset_scene()
    src = make_blob()
    out = tempfile.mkdtemp()
    rebuild_settings(target_faces=800, gr_candidates=1, gr_bake_dir=out, output_mode='REPLACE')
    res = run_op()
    assert res is src
    assert len(bpy.data.objects) == 1, "no LODs or temp objects in replace mode"
    files = os.listdir(out)
    assert files == ["blob_normal.png"], files


@case
def test_analyze_and_toggle():
    reset_scene()
    src = make_object("monkey")
    settings().target_faces = 800
    res = run_op()
    assert bpy.ops.auto_retopo.analyze() == {'FINISHED'}
    st = bpy.context.scene.auto_retopo_stats
    assert st.object_name == res.name and st.faces == len(res.data.polygons)
    assert st.quads > 0.9 * st.faces
    assert bpy.ops.auto_retopo.toggle_original() == {'FINISHED'}
    assert not src.hide_get() and res.hide_get()
    assert bpy.ops.auto_retopo.toggle_original() == {'FINISHED'}
    assert src.hide_get() and not res.hide_get()


@case
def test_manual_setup():
    reset_scene()
    src = make_object("monkey")
    assert bpy.ops.auto_retopo.manual_setup() == {'FINISHED'}
    ob = bpy.context.active_object
    assert ob.name == "monkey_manual_retopo" and ob.mode == 'EDIT'
    assert [m.type for m in ob.modifiers] == ['MIRROR', 'SHRINKWRAP']
    bpy.ops.object.mode_set(mode='OBJECT')


@case
def test_reset_settings():
    s = settings()
    s.target_faces = 123
    s.method = 'VOXEL'
    bpy.ops.auto_retopo.reset_settings()
    assert s.target_faces == 5000 and s.method == 'QUADRIFLOW'


@case
def test_ui_panels_draw():
    reset_scene()
    src = make_object("monkey")
    s = settings()
    log = []
    for method in ('QUADRIFLOW', 'VOXEL', 'DECIMATE', 'OCTREE', 'REBUILD'):
        s.method = method
        for dc in ('COLLAPSE', 'UNSUBDIV', 'DISSOLVE'):
            s.dc_mode = dc
            for density in ('FACES', 'RATIO', 'EDGE_LENGTH'):
                s.density_mode = density
                for output in ('NEW_OBJECT', 'REPLACE'):
                    s.output_mode = output
                    for sub in ('NONE', 'SUBSURF', 'MULTIRES'):
                        s.subdiv_mode = sub
                        for uv in ('NONE', 'SMART', 'LIGHTMAP'):
                            s.uv_mode = uv
                            s.oc_mode = 'SHARP'
                            for cls in ui.panels:
                                panel = FakePanel(log)
                                cls.draw(panel, bpy.context)
                                if hasattr(cls, "draw_header"):
                                    cls.draw_header(panel, bpy.context)
                                if hasattr(cls, "draw_header_preset"):
                                    cls.draw_header_preset(panel, bpy.context)
    s.method = 'REBUILD'
    for style in ('QUADS', 'ADAPTIVE', 'BEST'):
        s.gr_style = style
        for shading in ('SMOOTH_HARD', 'WEIGHTED', 'SMOOTH'):
            s.gr_shading = shading
            for flag in (False, True):
                s.gr_unwrap = s.gr_triangulate = s.gr_count_triangles = flag
                for cls in ui.panels:
                    cls.draw(FakePanel(log), bpy.context)
    # Panel visibility per method
    assert ui.AUTORETOPO_PT_game_finish.poll(bpy.context)
    assert not ui.AUTORETOPO_PT_subdiv.poll(bpy.context)
    s.method = 'QUADRIFLOW'
    assert not ui.AUTORETOPO_PT_game_finish.poll(bpy.context)
    assert ui.AUTORETOPO_PT_cleanup.poll(bpy.context)
    # Stats section, plain and with a Game-Ready report
    bpy.ops.auto_retopo.analyze()
    ui.AUTORETOPO_PT_tools.draw(FakePanel(log), bpy.context)
    bpy.context.scene.auto_retopo_stats.has_report = True
    ui.AUTORETOPO_PT_tools.draw(FakePanel(log), bpy.context)
    presets.AUTORETOPO_MT_quick_presets.draw(FakePanel(log), bpy.context)
    ui.AUTORETOPO_MT_object.draw(FakePanel(log), bpy.context)
    drawn = {name for kind, name in log if kind == "prop"}
    all_settings = set(presets.setting_names())
    missing = all_settings - drawn
    assert not missing, f"settings never shown in the UI: {sorted(missing)}"


@case
def test_category_change():
    ui.update_category("MyTab")
    assert ui.AUTORETOPO_PT_main.bl_category == "MyTab"
    assert ui.AUTORETOPO_PT_main.is_registered
    ui.update_category("Retopo")


@case
def test_reregister():
    auto_retopology.unregister()
    auto_retopology.register()
    assert hasattr(bpy.types.Scene, "auto_retopo")


# ---------------------------------------------------------------------------

def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    auto_retopology.register()
    failed = 0
    for func in RESULTS:
        try:
            func()
        except Exception:
            failed += 1
            print(f"FAIL {func.__name__}")
            traceback.print_exc()
        else:
            print(f"ok   {func.__name__}")
    auto_retopology.unregister()
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} tests passed")
    return failed


if __name__ == "__main__":
    code = main()
    if bpy.app.background:
        sys.exit(1 if code else 0)
