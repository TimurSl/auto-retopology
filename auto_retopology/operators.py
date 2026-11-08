# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators of the Auto Retopology add-on."""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator

from . import core


def get_prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _resolve_inputs(context, settings):
    """Objects to process. Selecting a previous result re-runs its original."""
    if settings.process_all_selected:
        candidates = [o for o in context.selected_objects if o.type == 'MESH']
        if context.active_object and context.active_object.type == 'MESH' \
                and context.active_object not in candidates:
            candidates.append(context.active_object)
    else:
        ob = context.active_object
        candidates = [ob] if ob and ob.type == 'MESH' else []

    inputs = []
    for ob in candidates:
        src = ob.auto_retopo_source
        if settings.output_mode == 'NEW_OBJECT' and src is not None and src.type == 'MESH' \
                and src.name in context.scene.objects:
            ob = src
        if ob not in inputs:
            inputs.append(ob)
    return inputs


def _store_report(context, info):
    """Put the Game-Ready Rebuild quality report into the Analysis panel."""
    res = info["object"]
    st = context.scene.auto_retopo_stats
    for key, value in core.mesh_stats(res.data).items():
        setattr(st, key, value)
    rep = info["report"]
    st.object_name = res.name
    st.has_report = True
    st.dev_avg = rep["rms_pct"]
    st.dev_max = rep["max_pct"]
    st.budget = rep["budget"]
    style = {'QUADS': "Clean Quads", 'DIRECT': "Clean Quads (direct)",
             'ADAPTIVE': "Adaptive"}.get(rep["style"], rep["style"])
    seed = f", seed {rep['seed']}" if rep["style"] in {'QUADS', 'DIRECT'} else ""
    st.winner = f"{style}{seed} (best of {rep['candidates']})"
    st.maps = ", ".join(img.name for img in info.get("images", [])) or "none"
    st.lods = ", ".join(f"{core.mesh_tri_count(o.data):,}" for o in info.get("lods", [])) or "none"


def _ensure_object_mode(context):
    if context.mode != 'OBJECT' and context.active_object is not None:
        bpy.ops.object.mode_set(mode='OBJECT')


class AUTORETOPO_OT_run(Operator):
    bl_idname = "auto_retopo.run"
    bl_label = "Auto Retopology"
    bl_description = ("Build clean new topology for the selected mesh objects using the "
                      "current Auto Retopology settings. Select a previous result to re-run it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        if ob is not None and ob.type == 'MESH':
            return True
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        settings = context.scene.auto_retopo
        prefs = get_prefs(context)
        max_res = prefs.max_voxel_resolution if prefs else 1024

        _ensure_object_mode(context)
        inputs = _resolve_inputs(context, settings)
        if not inputs:
            self.report({'ERROR'}, "Select at least one mesh object")
            return {'CANCELLED'}

        wm = context.window_manager
        steps_per_object = 12
        wm.progress_begin(0, len(inputs) * steps_per_object)
        counter = [0]

        def progress(name):
            counter[0] += 1
            wm.progress_update(counter[0])
            if prefs is None or prefs.verbose:
                print(f"[Auto Retopology] {name}")

        results, failures = [], []
        try:
            for i, ob in enumerate(inputs):
                counter[0] = i * steps_per_object
                try:
                    info = core.run_retopology(context, ob, settings, max_res, progress)
                except core.RetopoError as exc:
                    failures.append(f"{ob.name}: {exc}")
                    continue
                results.append(info)
                for w in info["warnings"]:
                    self.report({'WARNING'}, f"{ob.name}: {w}")
        finally:
            wm.progress_end()

        for msg in failures:
            self.report({'ERROR'}, msg)
        if not results:
            return {'CANCELLED'}

        context.view_layer.update()  # resync bases after objects were removed
        for o in context.view_layer.objects:
            if o is not None:
                o.select_set(False)
        for info in results:
            res = info["object"]
            if res.name in context.view_layer.objects:
                res.select_set(True)
                context.view_layer.objects.active = res

        if results[-1].get("report"):
            _store_report(context, results[-1])
        if len(results) == 1:
            r = results[0]
            if r.get("report"):
                rep = r["report"]
                self.report({'INFO'}, (f"{r['object'].name}: rebuilt with {rep['tris']:,} tris "
                                       f"(budget {rep['budget']:,}), deviation "
                                       f"{rep['rms_pct']:.2f}% avg / {rep['max_pct']:.2f}% max "
                                       f"in {r['time']:.1f}s"))
            else:
                self.report({'INFO'}, (f"{r['object'].name}: {r['input_faces']:,} -> "
                                       f"{r['output_faces']:,} faces in {r['time']:.1f}s"))
        else:
            total = sum(r['time'] for r in results)
            self.report({'INFO'}, f"Retopologized {len(results)} objects in {total:.1f}s"
                        + (f", {len(failures)} failed" if failures else ""))
        return {'FINISHED'}


class AUTORETOPO_OT_analyze(Operator):
    bl_idname = "auto_retopo.analyze"
    bl_label = "Analyze Mesh"
    bl_description = "Count faces, triangles, quads, n-gons, poles and problems of the active mesh"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        _ensure_object_mode(context)
        ob = context.active_object
        dg = context.evaluated_depsgraph_get()
        ob_eval = ob.evaluated_get(dg)
        me = ob_eval.to_mesh()
        try:
            stats = core.mesh_stats(me)
        finally:
            ob_eval.to_mesh_clear()
        target = context.scene.auto_retopo_stats
        target.object_name = ob.name
        target.has_report = False
        for key, value in stats.items():
            setattr(target, key, value)
        self.report({'INFO'}, f"{ob.name}: {stats['faces']:,} faces, {stats['quads']:,} quads, "
                              f"{stats['poles']:,} poles")
        return {'FINISHED'}


def _pair(context):
    """(result, source) for the active object, or (None, None)."""
    ob = context.active_object
    if ob is None:
        return None, None
    if ob.auto_retopo_source is not None:
        return ob, ob.auto_retopo_source
    results = core.find_previous_results(ob)
    if results:
        return results[0], ob
    return None, None


class AUTORETOPO_OT_toggle_original(Operator):
    bl_idname = "auto_retopo.toggle_original"
    bl_label = "Toggle Original / Result"
    bl_description = "Switch the viewport between the original object and its retopology"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        res, src = _pair(context)
        return res is not None and src is not None

    def execute(self, context):
        res, src = _pair(context)
        vl = context.view_layer
        if res.name not in vl.objects or src.name not in vl.objects:
            self.report({'ERROR'}, "Both objects must be in the current view layer")
            return {'CANCELLED'}
        show_src = src.hide_get() or src.display_type in {'WIRE', 'BOUNDS'}
        src.hide_set(not show_src)
        if show_src and src.display_type in {'WIRE', 'BOUNDS'}:
            src.display_type = 'TEXTURED'
        res.hide_set(show_src)
        visible = src if show_src else res
        for o in context.selected_objects:
            o.select_set(False)
        visible.select_set(True)
        vl.objects.active = visible
        return {'FINISHED'}


class AUTORETOPO_OT_apply_modifiers(Operator):
    bl_idname = "auto_retopo.apply_modifiers"
    bl_label = "Apply Retopo Modifiers"
    bl_description = ("Apply the live Shrinkwrap / Mirror / Data Transfer modifiers added by "
                      "Auto Retopology (Multires and Subdivision are kept)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and any(
            m.name.startswith(core.PREFIX) and m.type in {'SHRINKWRAP', 'MIRROR', 'DATA_TRANSFER'}
            for m in ob.modifiers)

    def execute(self, context):
        _ensure_object_mode(context)
        ob = context.active_object
        names = [m.name for m in ob.modifiers
                 if m.name.startswith(core.PREFIX) and m.type in {'SHRINKWRAP', 'MIRROR', 'DATA_TRANSFER'}]
        applied = 0
        with context.temp_override(**core.override(ob)):
            for name in names:
                try:
                    bpy.ops.object.modifier_apply(modifier=name)
                    applied += 1
                except RuntimeError as exc:
                    self.report({'WARNING'}, f"{name}: {exc}")
        self.report({'INFO'}, f"Applied {applied} modifier(s)")
        return {'FINISHED'}


class AUTORETOPO_OT_manual_setup(Operator):
    bl_idname = "auto_retopo.manual_setup"
    bl_label = "Manual Retopo Setup"
    bl_description = ("Create an empty retopology object over the active mesh with surface "
                      "snapping, shrinkwrap and mirror, ready for manual retopology")
    bl_options = {'REGISTER', 'UNDO'}

    use_mirror: BoolProperty(name="Mirror", default=True)
    mirror_axis: EnumProperty(name="Axis", items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")])
    offset: FloatProperty(name="Surface Offset", default=0.002, min=0.0, soft_max=0.1,
                          subtype='DISTANCE', precision=4)
    start_with: EnumProperty(
        name="Start With",
        items=[('PLANE', "Plane", "A small quad at the 3D cursor"),
               ('VERTEX', "Single Vertex", "A single vertex at the 3D cursor")],
    )

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        import bmesh
        from mathutils import Vector

        _ensure_object_mode(context)
        source = context.active_object
        name = source.name + "_manual_retopo"
        me = bpy.data.meshes.new(name)
        ob = bpy.data.objects.new(name, me)
        coll = source.users_collection[0] if source.users_collection else context.scene.collection
        coll.objects.link(ob)

        cursor = context.scene.cursor.location.copy()
        dims = source.dimensions
        size = max(max(dims) * 0.05, 1e-3)
        bm = bmesh.new()
        if self.start_with == 'PLANE':
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size * 0.5)
        else:
            bm.verts.new((0.0, 0.0, 0.0))
        bmesh.ops.translate(bm, verts=bm.verts[:], vec=Vector(cursor))
        bm.to_mesh(me)
        bm.free()

        sw = ob.modifiers.new(core.PREFIX + "Shrinkwrap", 'SHRINKWRAP')
        sw.target = source
        sw.wrap_mode = 'ABOVE_SURFACE'
        sw.offset = self.offset
        sw.show_on_cage = True
        sw.show_in_editmode = True
        if self.use_mirror:
            axis = core.AXIS_INDEX[self.mirror_axis]
            mr = core.add_mirror(ob, axis, 0.001)
            mr.mirror_object = source
            mr.show_on_cage = True
            mr.show_in_editmode = True
            ob.modifiers.move(1, 0)  # mirror before shrinkwrap so the center stays clipped

        ob.show_in_front = True
        ob.show_wire = True
        ob.color = (0.2, 0.6, 1.0, 1.0)

        ts = context.scene.tool_settings
        ts.use_snap = True
        try:
            ts.snap_elements = {'FACE_PROJECT'}
        except TypeError:
            ts.snap_elements = {'FACE'}
        if hasattr(ts, "use_snap_self"):
            ts.use_snap_self = False
        if hasattr(ts, "use_snap_backface_culling"):
            ts.use_snap_backface_culling = True

        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, "Manual retopology ready: extrude / poly-build on the surface")
        return {'FINISHED'}


classes = (
    AUTORETOPO_OT_run,
    AUTORETOPO_OT_analyze,
    AUTORETOPO_OT_toggle_original,
    AUTORETOPO_OT_apply_modifiers,
    AUTORETOPO_OT_manual_setup,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
