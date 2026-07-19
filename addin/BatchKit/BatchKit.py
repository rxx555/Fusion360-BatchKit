"""BatchKit - a light Fusion 360 add-in with two batch utilities.

1. Batch Rename : multi-select bodies (and/or components), type one base name,
   and each is named "<base> <n>" auto-incrementing in the order selected.
   Optional start index and zero-padding.
2. Batch Material : multi-select bodies spanning different components, pick one
   physical material, apply it to all of them at once.

Structural idioms (command definitions, SelectionCommandInput, inputChanged /
execute handlers, ribbon panel, run/stop) mirror the proven MassTrack add-in in
this repo. Risky API calls are guarded and logged to ~/.batchkit/batchkit.log so
a single bad entity never aborts the whole batch.
"""

import adsk.core
import adsk.fusion
import traceback
import os
import datetime

ADDIN_DIR = os.path.dirname(os.path.realpath(__file__))
DATA_DIR = os.path.join(os.path.expanduser("~"), ".batchkit")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    pass
LOG_PATH = os.path.join(DATA_DIR, "batchkit.log")

CMDS = [
    ("bkRename", "BatchKit: Batch Rename",
     "Select bodies (and/or components) in the order you want numbered, type a "
     "base name, and each becomes '<base> 1', '<base> 2', ... Optional start "
     "index and zero-padding. Not undoable: Fusion does not add API renames to "
     "the undo stack, so Ctrl/Cmd-Z will not restore the previous names. Re-run "
     "to rename again."),
    ("bkMaterial", "BatchKit: Batch Material",
     "Select bodies (across any components), pick one physical material, and it "
     "is applied to all of them at once."),
    ("bkSelectByMaterial", "BatchKit: Select by Material",
     "Pick a material and select every body that uses it across the whole "
     "assembly, ready to rename, re-material, or edit them together."),
]

_app = None
_ui = None
_handlers = []
_controls = []
_cmd_defs = []
_material_names = None      # cached sorted list for the dropdown


def _log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write("%s  %s\n" % (
                datetime.datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Material lookup
# ----------------------------------------------------------------------------
def _material_libraries():
    """Yield every MaterialLibrary. Appearance-only libraries naturally
    contribute nothing because their .materials collection is empty."""
    try:
        libs = _app.materialLibraries
        for i in range(libs.count):
            yield libs.item(i)
    except Exception:
        _log("materialLibraries enumerate failed:\n" + traceback.format_exc())


def _all_material_names():
    """Unique, sorted physical-material names from the document plus every
    material library. Cached after first build."""
    global _material_names
    if _material_names is not None:
        return _material_names
    names = set()
    try:
        design = adsk.fusion.Design.cast(_app.activeProduct)
        if design:
            for i in range(design.materials.count):
                names.add(design.materials.item(i).name)
    except Exception:
        _log("design.materials enumerate failed:\n" + traceback.format_exc())
    for lib in _material_libraries():
        try:
            mats = lib.materials
            for j in range(mats.count):
                names.add(mats.item(j).name)
        except Exception:
            _log("library materials enumerate failed (%s):\n%s"
                 % (getattr(lib, "name", "?"), traceback.format_exc(limit=1)))
    _material_names = sorted(names, key=lambda s: s.lower())
    return _material_names


def _resolve_material(name):
    """Find a Material by exact name: the document first (so already-loaded
    materials win), then each library. Returns the Material or None."""
    if not name:
        return None
    try:
        design = adsk.fusion.Design.cast(_app.activeProduct)
        if design:
            m = design.materials.itemByName(name)
            if m:
                return m
    except Exception:
        _log("design.materials.itemByName failed:\n" + traceback.format_exc())
    for lib in _material_libraries():
        try:
            m = lib.materials.itemByName(name)
            if m:
                return m
        except Exception:
            pass
    return None


def _mat_label(name, count):
    """Dropdown label pairing a material with how many bodies it will select."""
    return "%s (%d %s)" % (name, count, "body" if count == 1 else "bodies")


def _bodies_by_material(design):
    """Ordered [(material_name, [native BRepBody, ...])] over the whole design.
    A body's material is read as body.material.name (else '(none)'), the same
    name Batch Material writes, so applying and selecting agree."""
    groups, order = {}, []
    for comp in design.allComponents:
        for body in comp.bRepBodies:
            name = body.material.name if body.material else "(none)"
            if name not in groups:
                groups[name] = []
                order.append(name)
            groups[name].append(body)
    order.sort(key=lambda s: s.lower())
    return [(name, groups[name]) for name in order]


def _instance_count(root, bodies):
    """Physical bodies the selection will touch: each native body counted once
    per occurrence of its parent component (root-level bodies count once)."""
    total = 0
    for b in bodies:
        occs = root.allOccurrencesByComponent(b.parentComponent)
        total += occs.count if (occs and occs.count > 0) else 1
    return total


# ----------------------------------------------------------------------------
# Batch operations
# ----------------------------------------------------------------------------
def _native_body(body):
    """A canvas/browser selection inside a component instance is a proxy body;
    name/material must be set on its nativeObject so the change lands on the
    real body (and applies to every instance)."""
    return body.nativeObject if body.nativeObject else body


def _apply_rename(entities, base, start, pad, step):
    """Number entities in selection order. Returns (renamed, errors, warnings)."""
    n, errors, warnings = 0, [], []
    seen_components = {}       # entityToken -> newname, to warn on shared components
    for i, ent in enumerate(entities):
        num = start + i * step
        num_s = str(num).zfill(pad) if pad > 0 else str(num)
        newname = ("%s %s" % (base, num_s)) if base else num_s
        try:
            occ = adsk.fusion.Occurrence.cast(ent)
            body = adsk.fusion.BRepBody.cast(ent)
            comp = adsk.fusion.Component.cast(ent)
            if occ:
                target = occ.component
            elif comp:
                target = comp
            elif body:
                target = _native_body(body)
            else:
                errors.append("skipped unsupported selection #%d" % (i + 1))
                continue
            # Two instances of one component can't hold two different names.
            if occ or comp:
                tok = target.entityToken
                if tok in seen_components:
                    warnings.append(
                        "component '%s' selected more than once; kept last name"
                        % target.name)
                seen_components[tok] = newname
            target.name = newname
            n += 1
        except Exception:
            errors.append("#%d -> '%s': %s"
                          % (i + 1, newname, traceback.format_exc(limit=1)))
            _log("rename failed #%d:\n%s" % (i + 1, traceback.format_exc()))
    return n, errors, warnings


def _apply_material(entities, mat):
    """Apply one Material to every selected body; an occurrence expands to all
    bodies in its component. Returns (applied, errors)."""
    n, errors = 0, []
    for ent in entities:
        try:
            occ = adsk.fusion.Occurrence.cast(ent)
            body = adsk.fusion.BRepBody.cast(ent)
            comp = adsk.fusion.Component.cast(ent)
            targets = []
            if occ:
                targets = [b for b in occ.component.bRepBodies]
            elif comp:
                targets = [b for b in comp.bRepBodies]
            elif body:
                targets = [_native_body(body)]
            for b in targets:
                try:
                    b.material = mat
                    n += 1
                except Exception:
                    errors.append("body '%s': %s"
                                  % (getattr(b, "name", "?"),
                                     traceback.format_exc(limit=1)))
                    _log("material set failed:\n" + traceback.format_exc())
        except Exception:
            errors.append(traceback.format_exc(limit=1))
            _log("material apply failed:\n" + traceback.format_exc())
    return n, errors


# ----------------------------------------------------------------------------
# Command wiring
# ----------------------------------------------------------------------------
class _CmdCreated(adsk.core.CommandCreatedEventHandler):
    def __init__(self, cmd_id):
        super().__init__()
        self.cmd_id = cmd_id

    def notify(self, args):
        try:
            cmd = args.command
            inputs = cmd.commandInputs

            if self.cmd_id in ("bkRename", "bkMaterial"):
                sel = inputs.addSelectionInput(
                    "sel", "Selection",
                    "Bodies (and components) - pick in numbering order")
                sel.addSelectionFilter("Bodies")
                sel.addSelectionFilter("Occurrences")
                sel.setSelectionLimits(1, 0)

            if self.cmd_id == "bkRename":
                inputs.addStringValueInput("base", "Base name", "Part")
                inputs.addStringValueInput("start", "Start number", "1")
                inputs.addStringValueInput("pad", "Zero-pad width (0 = none)", "0")
            elif self.cmd_id == "bkMaterial":
                dd = inputs.addDropDownCommandInput(
                    "mat", "Material",
                    adsk.core.DropDownStyles.TextListDropDownStyle)
                names = _all_material_names()
                for nm in names:
                    dd.listItems.add(nm, False)
                if dd.listItems.count > 0:
                    dd.listItems.item(0).isSelected = True
                inputs.addStringValueInput(
                    "matname", "Material name override (optional)", "")
            elif self.cmd_id == "bkSelectByMaterial":
                design = adsk.fusion.Design.cast(_app.activeProduct)
                dd = inputs.addDropDownCommandInput(
                    "material", "Material",
                    adsk.core.DropDownStyles.TextListDropDownStyle)
                groups = _bodies_by_material(design) if design else []
                if groups:
                    root = design.rootComponent
                    for i, (name, bodies) in enumerate(groups):
                        dd.listItems.add(
                            _mat_label(name, _instance_count(root, bodies)),
                            i == 0)
                else:
                    dd.listItems.add("(no bodies in this design)", True)

            on_exec = _CmdExecute(self.cmd_id)
            cmd.execute.add(on_exec)
            _handlers.append(on_exec)
        except Exception:
            if _ui:
                _ui.messageBox("BatchKit error:\n" + traceback.format_exc())


class _CmdExecute(adsk.core.CommandEventHandler):
    def __init__(self, cmd_id):
        super().__init__()
        self.cmd_id = cmd_id

    def notify(self, args):
        try:
            design = adsk.fusion.Design.cast(_app.activeProduct)
            if not design:
                _ui.messageBox("No active design.", "BatchKit")
                return
            inputs = args.command.commandInputs
            if self.cmd_id in ("bkRename", "bkMaterial"):
                sel_input = inputs.itemById("sel")
                ents = [sel_input.selection(i).entity
                        for i in range(sel_input.selectionCount)]
                if not ents:
                    _ui.messageBox("Nothing selected.", "BatchKit")
                    return

            if self.cmd_id == "bkRename":
                base = inputs.itemById("base").value.strip()
                try:
                    start = int(inputs.itemById("start").value.strip() or "1")
                except ValueError:
                    start = 1
                try:
                    pad = int(inputs.itemById("pad").value.strip() or "0")
                except ValueError:
                    pad = 0
                if pad < 0:
                    pad = 0
                n, errors, warnings = _apply_rename(ents, base, start, pad, 1)
                msg = "Renamed %d item(s)." % n
                if warnings:
                    msg += "\n\n" + "\n".join(warnings[:8])
                if errors:
                    msg += ("\n\n%d failed (see the BatchKit log):\n  %s"
                            % (len(errors), "\n  ".join(errors[:5])))
                _ui.messageBox(msg, "BatchKit")

            elif self.cmd_id == "bkMaterial":
                override = inputs.itemById("matname").value.strip()
                dd = inputs.itemById("mat")
                chosen = override or (
                    dd.selectedItem.name if dd.selectedItem else "")
                if not chosen:
                    _ui.messageBox("Pick a material or type a name.", "BatchKit")
                    return
                mat = _resolve_material(chosen)
                if not mat:
                    _ui.messageBox(
                        "Material '%s' not found in the document or any "
                        "material library. Check the exact name." % chosen,
                        "BatchKit")
                    return
                n, errors = _apply_material(ents, mat)
                msg = "Applied '%s' to %d body/bodies." % (mat.name, n)
                if errors:
                    msg += ("\n\n%d failed (see the BatchKit log):\n  %s"
                            % (len(errors), "\n  ".join(errors[:5])))
                _ui.messageBox(msg, "BatchKit")

            elif self.cmd_id == "bkSelectByMaterial":
                groups = _bodies_by_material(design)
                if not groups:
                    _ui.messageBox("This design has no bodies.", "BatchKit")
                    return
                inp = inputs.itemById("material")
                chosen = inp.selectedItem.name if inp and inp.selectedItem else None
                root = design.rootComponent
                bodies = None
                for name, blist in groups:
                    if _mat_label(name, _instance_count(root, blist)) == chosen:
                        bodies = blist
                        break
                if bodies is None:
                    return
                sels = _ui.activeSelections
                sels.clear()
                n = 0
                for body in bodies:
                    occs = root.allOccurrencesByComponent(body.parentComponent)
                    if occs and occs.count > 0:
                        for i in range(occs.count):
                            try:
                                sels.add(body.createForAssemblyContext(
                                    occs.item(i)))
                                n += 1
                            except Exception:
                                _log("select-by-material: no proxy for body "
                                     "'%s' in %s" % (body.name,
                                                     occs.item(i).fullPathName))
                    else:                        # root-level body: native is valid
                        try:
                            sels.add(body)
                            n += 1
                        except Exception:
                            _log("select-by-material: cannot select body '%s'"
                                 % body.name)
                if n == 0:
                    _ui.messageBox("No bodies could be selected for that "
                                   "material.", "BatchKit")
        except Exception:
            if _ui:
                _ui.messageBox("BatchKit error:\n" + traceback.format_exc())


# ----------------------------------------------------------------------------
# Ribbon panel + lifecycle
# ----------------------------------------------------------------------------
PANEL_ID = "BatchKitPanel"


def _make_panel():
    """BatchKit panel in the Solid tab, falling back to the ADD-INS panel."""
    try:
        ws = _ui.workspaces.itemById("FusionSolidEnvironment")
        tab = ws.toolbarTabs.itemById("SolidTab") or ws.toolbarTabs.item(0)
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = tab.toolbarPanels.add(PANEL_ID, "BatchKit")
        return panel
    except Exception:
        _log("panel create failed, using ADD-INS panel:\n"
             + traceback.format_exc())
        return _ui.allToolbarPanels.itemById("SolidScriptsAddinsPanel")


def run(context):
    global _app, _ui
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface
        panel = _make_panel()
        res = os.path.join(ADDIN_DIR, "resources")
        for cmd_id, cmd_name, tooltip in CMDS:
            existing = _ui.commandDefinitions.itemById(cmd_id)
            if existing:
                existing.deleteMe()
            icon = os.path.join(res, cmd_id)
            if os.path.isdir(icon):
                cmd_def = _ui.commandDefinitions.addButtonDefinition(
                    cmd_id, cmd_name, tooltip, icon)
            else:
                cmd_def = _ui.commandDefinitions.addButtonDefinition(
                    cmd_id, cmd_name, tooltip)
            on_created = _CmdCreated(cmd_id)
            cmd_def.commandCreated.add(on_created)
            _handlers.append(on_created)
            _cmd_defs.append(cmd_def)
            if panel:
                ctl = panel.controls.itemById(cmd_id)
                if ctl:
                    ctl.deleteMe()
                ctl = panel.controls.addCommand(cmd_def)
                ctl.isPromoted = True
                ctl.isPromotedByDefault = True
                _controls.append(ctl)
        _log("add-in started")
    except Exception:
        if _ui:
            _ui.messageBox("BatchKit failed to start:\n"
                           + traceback.format_exc())


def stop(context):
    try:
        for ctl in _controls:
            try:
                ctl.deleteMe()
            except Exception:
                pass
        for cd in _cmd_defs:
            try:
                cd.deleteMe()
            except Exception:
                pass
        _controls.clear()
        _cmd_defs.clear()
        _handlers.clear()
        _log("add-in stopped")
    except Exception:
        pass
