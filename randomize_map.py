# Shuffle the loose contents of the twin-hall map: the things lying on the floor (crates,
# cartons, cans, pouches, finished units, trolleys, signs) and the cargo in the pallet racks.
# Run it again for another arrangement. The structure of the map - walls, columns, rack
# frames, shelves, zone paint, the passage - never moves.
#
# What changes, per item:
#   moved     a new position, inside the rack deck it was on, or within a radius of where
#             it was on the floor
#   turned    a new heading (floor items freely, rack cargo only slightly, the way pallets
#             are actually squared up to a shelf)
#   stacked   occasionally set down on top of another item instead of on the floor
#   removed   occasionally taken out of the scene altogether (deactivated), as if it had
#             been picked
# Every run starts by putting back whatever an earlier run removed, so the map never drains
# away as you re-run it.
#
# Two ways to run it:
#   1. Isaac Sim > Window > Script Editor, with the map open: paste and run. It edits the
#      open stage in place and prints a before/after table. Save the stage if you want it.
#   2. standalone (needs `pip install usd-core`, no Isaac):
#        python randomize_map.py twin_hall_map.usd -o shuffled.usd
#      --seed 7 reproduces an arrangement, --in-place overwrites the input.
from pxr import Usd, UsdGeom, Gf
import argparse
import json
import math
import os
import random

# ------------------------------------------------------------------ parameters
JITTER = 2.0          # how far a floor item may travel from where it started, in meters
TRIES = 120           # placement attempts per item before it is left where it is
CLEARANCE = 0.05      # gap kept between two objects, in meters
P_REMOVE = 0.12       # chance an item is taken out of the scene entirely
P_STACK = 0.18        # chance an item is set down on top of another one
SEED = None           # None = a different arrangement every run; an int reproduces one
REPORT_ROWS = 14      # how many before/after lines to print (the rest are summarised)

# prims whose name starts with one of these, under the matching parent, are movable.
# Everything else is structure, and is treated as an obstacle.
FLOOR_GROUPS = {
    "Storage":  ("Stock", "Stage"),
    "PickZone": ("f_", "g_"),
    "Products": ("Vac_",),
    "Assets":   ("Clutter_", "Extra_"),
}
RACK_GROUPS = {"Storage": ("RackLoad_", "RackCornerLoad_")}
PART_SUFFIXES = ("_base", "_wallN", "_wallS", "_wallE", "_wallW")   # the parts of one crate
PASSAGE_BAND_Y = (-7.3, -1.3)   # the band the doorway opens onto, kept clear in both halls
PASSAGE_BAND_X = 6.0            # how far either side of the shared wall to keep clear


def get_stage_and_args():
    try:
        import omni.usd
        stage = omni.usd.get_context().get_stage()
    except Exception:
        stage = None
    if stage is not None:
        return stage, None
    ap = argparse.ArgumentParser(description="Shuffle the loose contents of the twin-hall map.")
    ap.add_argument("input", help="map .usd to read")
    ap.add_argument("-o", "--output", help="where to write the shuffled map")
    ap.add_argument("--in-place", action="store_true", help="overwrite the input file")
    ap.add_argument("--seed", type=int, default=SEED, help="reproduce a given arrangement")
    ap.add_argument("--jitter", type=float, default=JITTER, help="floor-item travel radius (m)")
    ap.add_argument("--remove", type=float, default=P_REMOVE, help="chance an item is removed")
    ap.add_argument("--report", help="also write the before/after table to this JSON file")
    args = ap.parse_args()
    if not args.output and not args.in_place:
        ap.error("give -o OUTPUT, or --in-place to overwrite the input")
    if args.output and not args.in_place:
        # hold on to the stage AND its layer: dropping the stage here would free the layer
        # out from under Export()
        src = Usd.Stage.Open(args.input)
        src_layer = src.GetRootLayer()
        src_layer.Export(args.output)
        del src_layer, src
        return Usd.Stage.Open(args.output), args
    return Usd.Stage.Open(args.input), args


stage, ARGS = get_stage_and_args()
if ARGS is not None:
    JITTER, P_REMOVE = ARGS.jitter, ARGS.remove
RNG = random.Random(SEED if ARGS is None else ARGS.seed)
CACHE = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                          [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])


def world_range(prim):
    r = CACHE.ComputeWorldBound(prim).ComputeAlignedRange()
    return None if r.IsEmpty() else r


def top_translate_op(prim):
    # the topmost translate op on this branch - children below it are placed relative to it
    stack = [prim]
    while stack:
        p = stack.pop()
        ops = [op for op in UsdGeom.Xformable(p).GetOrderedXformOps()
               if op.GetOpType() == UsdGeom.XformOp.TypeTranslate]
        if ops:
            return ops[0]
        stack.extend(p.GetChildren())
    return None


def rotate_op(prim):
    ops = [op for op in UsdGeom.Xformable(prim).GetOrderedXformOps()
           if op.GetOpType() == UsdGeom.XformOp.TypeRotateZ]
    return ops[0] if ops else None


def overlaps(a, b):
    return not Gf.Range3d(a).IntersectWith(b).IsEmpty()


def pad2d(r, m):
    return Gf.Range3d(r.GetMin() - Gf.Vec3d(m, m, 0.0), r.GetMax() + Gf.Vec3d(m, m, 0.0))


def rotated_half_extent(size, deg):
    c, s = abs(math.cos(math.radians(deg))), abs(math.sin(math.radians(deg)))
    return (size[0] * c + size[1] * s) / 2.0, (size[0] * s + size[1] * c) / 2.0


# ------------------------------------------------------------------ collect
root = stage.GetPrimAtPath("/World/TwinHall")
halls = [p for p in root.GetChildren() if p.GetName().startswith("Hall_")] if root else []
if not halls:
    raise RuntimeError("/World/TwinHall/Hall_* not found - is the twin-hall map open?")


class Unit(object):
    # one movable thing: a single prim, or the several prims that make up one crate, or an
    # item and whatever is stacked on top of it
    def __init__(self, key, kind, hall):
        self.key, self.kind, self.hall = key, kind, hall
        self.prims = []
        self.range = Gf.Range3d()
        self.removed = False

    def add(self, prim, r):
        self.prims.append(prim)
        self.range.UnionWith(r)


units = {}
movable_paths = set()
for hall in halls:
    for sub, prefixes in list(FLOOR_GROUPS.items()) + list(RACK_GROUPS.items()):
        parent = stage.GetPrimAtPath(hall.GetPath().AppendChild(sub))
        if not parent:
            continue
        rack_prefixes = RACK_GROUPS.get(sub, ())
        # GetAllChildren, not GetChildren: an item an earlier run deactivated is filtered
        # out of the default traversal, and would never be put back
        for child in parent.GetAllChildren():
            name = child.GetName()
            pre = next((p for p in prefixes if name.startswith(p)), None)
            if pre is None:
                continue
            child.SetActive(True)                  # undo an earlier run's removals
            r = world_range(child)
            if r is None:
                continue
            base = name
            for suf in PART_SUFFIXES:              # the walls of a crate are one item
                if base.endswith(suf):
                    base = base[: -len(suf)]
                    break
            if pre not in rack_prefixes and base[-2:-1] == "_" and base[:-2].startswith(pre):
                base = base[:-2]                   # a stack of crates travels together
            key = str(parent.GetPath()) + "/" + base
            u = units.setdefault(key, Unit(key, "rack" if pre in rack_prefixes else "floor", hall))
            u.add(child, r)
            movable_paths.add(str(child.GetPath()))

# structure: everything else that stands above the floor paint
obstacles = []
for prim in stage.Traverse():
    if not prim.IsA(UsdGeom.Gprim):
        continue
    path = str(prim.GetPath())
    if path in movable_paths or any(path.startswith(m + "/") for m in movable_paths):
        continue
    r = world_range(prim)
    if r is None or r.GetMax()[2] < 0.06:
        continue
    obstacles.append(r)

# rack decks, so rack cargo stays on the shelf it belongs to
decks = {}
for hall in halls:
    storage = stage.GetPrimAtPath(hall.GetPath().AppendChild("Storage"))
    if not storage:
        continue
    for child in storage.GetChildren():
        n = child.GetName()
        if "_deck" not in n:
            continue
        r = world_range(child)
        if r is not None:
            decks.setdefault(str(storage.GetPath()) + "/" + n.split("_deck")[0], []).append(r)

seam_x = None
jw = stage.GetPrimAtPath("/World/TwinHall/Junction/WallNorth")
if jw is not None and world_range(jw) is not None:
    seam_x = world_range(jw).GetMidpoint()[0]


def in_passage(r):
    if seam_x is None:
        return False
    if r.GetMax()[1] < PASSAGE_BAND_Y[0] or r.GetMin()[1] > PASSAGE_BAND_Y[1]:
        return False
    return r.GetMin()[0] < seam_x + PASSAGE_BAND_X and r.GetMax()[0] > seam_x - PASSAGE_BAND_X


def allowed_region(u):
    if u.kind == "floor":
        c = u.range.GetMidpoint()
        return c[0] - JITTER, c[0] + JITTER, c[1] - JITTER, c[1] + JITTER
    name = u.key.rsplit("/", 1)[1]
    rack = str(u.hall.GetPath()) + "/Storage/" + name.replace("Load", "", 1).rsplit("_", 3)[0]
    d = decks.get(rack)
    if not d:
        return None
    z = u.range.GetMin()[2]
    on = [k for k in d if abs(k.GetMax()[2] - z) < 0.12]
    if not on:                                     # the bottom bay: straight on the floor
        span = Gf.Range3d()
        for k in d:
            span.UnionWith(k)
        on = [span]
    return (on[0].GetMin()[0], on[0].GetMax()[0], on[0].GetMin()[1], on[0].GetMax()[1])


# ------------------------------------------------------------------ shuffle
# Every unit's current footprint is reserved up front. Without that, a unit placed early
# can be dropped onto the spot a later unit still occupies, and that later unit - if it
# then fails to find a free spot of its own - stays put, interpenetrating it.
order = list(units.values())
RNG.shuffle(order)
reserved = {u.key: Gf.Range3d(u.range) for u in order}
report, moved, turned, stacked, removed, stuck = [], 0, 0, 0, 0, 0

for u in order:
    before = u.range.GetMidpoint()
    if RNG.random() < P_REMOVE:
        for p in u.prims:
            p.SetActive(False)
        u.removed = True
        del reserved[u.key]
        removed += 1
        report.append((u.key, before, None, "removed"))
        continue

    region = allowed_region(u)
    op_pairs = [(p, top_translate_op(p), rotate_op(p)) for p in u.prims]
    if region is None or any(op is None for _, op, _ in op_pairs):
        continue
    x0, x1, y0, y1 = region
    size = u.range.GetSize()
    can_turn = all(rot is not None for _, _, rot in op_pairs)
    near = [o for o in obstacles
            if overlaps(Gf.Range3d(Gf.Vec3d(x0 - 2.0, y0 - 2.0, u.range.GetMin()[2]),
                                   Gf.Vec3d(x1 + 2.0, y1 + 2.0, u.range.GetMax()[2])), o)]
    others = [r for k, r in reserved.items() if k != u.key]

    # sometimes set it down on top of something else that is already in place
    stack_on = None
    if u.kind == "floor" and RNG.random() < P_STACK:
        here = u.range.GetMidpoint()
        cand = [r for k, r in reserved.items()
                if k != u.key and 0.15 < r.GetMax()[2] < 0.85
                and r.GetSize()[0] > size[0] * 0.8 and r.GetSize()[1] > size[1] * 0.8
                and abs(r.GetMidpoint()[0] - here[0]) < JITTER
                and abs(r.GetMidpoint()[1] - here[1]) < JITTER]
        if cand:
            stack_on = RNG.choice(cand)

    placed = False
    for _ in range(TRIES):
        deg = (RNG.uniform(-180.0, 180.0) if u.kind == "floor" else RNG.uniform(-9.0, 9.0)) \
            if can_turn else None
        hw, hh = rotated_half_extent(size, deg or 0.0)
        if stack_on is not None:
            c = stack_on.GetMidpoint()
            cx = c[0] + RNG.uniform(-0.06, 0.06)
            cy = c[1] + RNG.uniform(-0.06, 0.06)
            z_base = stack_on.GetMax()[2]
        else:
            cx = RNG.uniform(min(x0 + hw, x1 - hw), max(x0 + hw, x1 - hw))
            cy = RNG.uniform(min(y0 + hh, y1 - hh), max(y0 + hh, y1 - hh))
            z_base = u.range.GetMin()[2]
        dz = z_base - u.range.GetMin()[2]
        cand = Gf.Range3d(Gf.Vec3d(cx - hw, cy - hh, u.range.GetMin()[2] + dz),
                          Gf.Vec3d(cx + hw, cy + hh, u.range.GetMax()[2] + dz))
        if in_passage(cand):
            continue
        padded = pad2d(cand, CLEARANCE)
        if any(overlaps(padded, o) for o in near):
            continue
        if any(overlaps(padded, r) for r in others
               if not (stack_on is not None and r == stack_on)):
            continue
        d = u.range.GetMidpoint()
        for p, op, rot in op_pairs:
            v = op.Get()
            op.Set(Gf.Vec3d(v[0] + (cx - d[0]), v[1] + (cy - d[1]), v[2] + dz))
            if deg is not None and rot is not None:
                rot.Set(rot.Get() + deg)
        reserved[u.key] = cand
        placed = True
        moved += 1
        turned += 1 if deg is not None else 0
        stacked += 1 if stack_on is not None else 0
        report.append((u.key, before, cand.GetMidpoint(),
                       "stacked" if stack_on is not None else "moved"))
        break
    if not placed:
        stuck += 1
        report.append((u.key, before, before, "kept"))

# ------------------------------------------------------------------ before / after
print("")
print("[randomize_map] before -> after")
print("  %-42s %-21s %-21s %s" % ("item", "was (x, y, z)", "now (x, y, z)", "change"))
shown = [r for r in report if r[3] != "kept"][:REPORT_ROWS]
for key, a, b, what in shown:
    name = key.split("/World/TwinHall/")[-1]
    if b is None:
        print("  %-42s (%6.2f,%7.2f,%5.2f)  %-21s %s" % (name, a[0], a[1], a[2], "-", what))
    else:
        print("  %-42s (%6.2f,%7.2f,%5.2f)  (%6.2f,%7.2f,%5.2f)  %s"
              % (name, a[0], a[1], a[2], b[0], b[1], b[2], what))
rest = len([r for r in report if r[3] != "kept"]) - len(shown)
if rest > 0:
    print("  ... and %d more" % rest)
print("[randomize_map] %d items: %d moved (%d turned, %d stacked), %d removed, %d kept, seed=%s"
      % (len(order), moved, turned, stacked, removed, stuck,
         "random" if (ARGS is None or ARGS.seed is None) else ARGS.seed))

if ARGS is not None:
    stage.GetRootLayer().Save()
    print("[randomize_map] saved:", os.path.abspath(ARGS.output or ARGS.input))
    if ARGS.report:
        with open(ARGS.report, "w") as fh:
            json.dump([{"item": k, "was": list(a), "now": (list(b) if b else None), "change": w}
                       for k, a, b, w in report], fh, indent=1)
        print("[randomize_map] report:", os.path.abspath(ARGS.report))
