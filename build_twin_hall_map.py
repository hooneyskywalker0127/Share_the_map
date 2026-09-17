# Twin-hall warehouse map - two identical halls joined side by side (along +X) with a
# single 5 m passage cut into the shared wall. Built from primitives only (plus optional
# NVIDIA Isaac warehouse props), so it loads without any project-specific asset.
#
# What is in one hall:
#   floor + painted main aisle / work zone / tile-grout lines, a narrow back shortcut with
#   a low step, storage racks + rigid-body crate stacks, a picking bay (cartons / cans /
#   pouches on low shelves and on the floor), a packing zone with an outbound-carton stock,
#   a handover strip, a stalled-robot obstacle in the aisle, perimeter walls + columns, and
#   ceiling/task lighting. NO assembly stations and no robots - the map is meant to be
#   driven by whatever robot the user spawns into it.
#
# Two ways to run it:
#   1. standalone (no Isaac needed, needs `pip install usd-core`):
#        python build_twin_hall_map.py -o twin_hall_map.usd
#      writes a self-contained USD you can open in Isaac Sim / usdview.
#   2. pasted into Isaac Sim > Script Editor: it builds straight into the open stage
#      (props are then resolved through the local Isaac asset root).
#
# Everything is authored in meters, Z-up.
from pxr import Usd, UsdGeom, UsdLux, UsdShade, UsdPhysics, Sdf, Gf
import argparse
import math
import os
import random

# ------------------------------------------------------------------ parameters
# Hall layout. There is no assembly line in the hall anymore, but the aisle, the painted
# work zone and the hall footprint are still derived from this nominal cell pitch, so the
# proportions stay the same as the original layout.
CELL_COUNT      = 4
CELL_SPACING    = 4.5
CELL_Y          = 0.0       # centre line of the (now empty) work zone
WORK_TOP        = 0.75      # nominal working height, used by a couple of props

CORRIDOR_WIDTH  = 6.0       # main aisle - two mobile bases abreast, so one stopped mid-aisle
                            # can still be passed
AISLE_RUNWAY    = 3.0       # aisle length past the ends of the work zone (accel/turn room)
SHORTCUT_WIDTH  = 1.1       # narrow back path, too tight for a wide mobile base
SHORTCUT_Y      = 2.2       # the back leg runs behind the work zone
STEP_HEIGHT     = 0.12      # low step gating the shortcut: a legged robot steps over it,
                            # a wheeled base cannot climb it
WALL_H          = 4.0

PACK_W          = 3.4       # packing zone width (worktable + outbound cartons)
BOUND_W         = 0.5       # handover strip width between the picking bay and the pack zone

# ---- twin hall ----
HALL_GAP        = 5.0       # width of the passage cut into the shared wall
HALL_GAP_Y      = None      # None = passage lined up with the main aisle, else an absolute y
JOIN_MARGIN     = 1.5       # margin between the last fixture and the shared wall

SHOW_CEILING    = False     # overhead light panels + truss beams + cable trays. OFF = open
                            # top so the interior is visible from above/angled views.
USE_ASSETS      = True      # dress storage/clutter with the NVIDIA Isaac warehouse USDs
USE_EXTRA       = True      # extra props (trolleys, wall props, signs, storage crates)
USE_PHYSICS     = True      # colliders on the structure + rigid bodies on the loose crates,
                            # so they behave physically on Play
USE_PRODUCTS    = True      # loose finished units staged on the storage floor (rigid bodies)
USE_PICKZONE    = True      # mixed picking bay south of the aisle: low shelves + goods piled
                            # irregularly on the floor (cartons / cans / pouches)
USE_PACKSTATION = True      # packing zone east of the picking bay + handover strip + the
                            # loading-dock door. Meaningless without USE_PICKZONE.
USE_OBSTACLE    = True      # a stalled-robot placeholder blocking the main aisle (a real
                            # static collider) with dashed detour markings around it
USE_SEMANTICS   = True      # tag prims (floor/walls/crates/goods/racks/dock) with class
                            # labels for segmentation ground truth. Needs Isaac Sim - in a
                            # standalone run this is a no-op.
JITTER_SEED     = 20260724  # fixed seed so the "messy" placement (crate stacks, floor wear,
                            # light intensity) is reproducible across re-runs

RNG = random.Random(JITTER_SEED)

# Isaac warehouse props (paths relative to the Isaac asset root)
PALLET     = "/Isaac/Props/Pallet/pallet.usd"
BOXA = "/Isaac/Environments/Simple_Warehouse/Props/SM_CardBoxA_01.usd"
BOXB = "/Isaac/Environments/Simple_Warehouse/Props/SM_CardBoxB_01.usd"
BOXC = "/Isaac/Environments/Simple_Warehouse/Props/SM_CardBoxC_01.usd"
PUSHCART  = "/Isaac/Environments/Simple_Warehouse/Props/SM_PushcartA_02.usd"
EXTING    = "/Isaac/Environments/Simple_Warehouse/Props/SM_FireExtinguisher_02.usd"
FUSEBOX   = "/Isaac/Environments/Simple_Warehouse/Props/SM_FuseBox_01.usd"
AISLESIGN = "/Isaac/Environments/Simple_Warehouse/Props/S_AisleSign.usd"
CRATE     = "/Isaac/Environments/Simple_Warehouse/Props/SM_CratePlastic_B_01.usd"
# default CDN copy of the Isaac assets, used when we are not running inside Isaac Sim
ASSET_ROOT_CDN = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
# --------------------------------------------------------------------------

# ---- stage: the open Isaac Sim stage if there is one, else a new USD file ----
try:
    import omni.usd
    stage = omni.usd.get_context().get_stage()
except Exception:
    stage = None

ARGS = None
if stage is None:                       # standalone run -> author into a fresh USD file
    ap = argparse.ArgumentParser(description="Build the twin-hall warehouse map.")
    ap.add_argument("-o", "--output", default="twin_hall_map.usd",
                    help="output .usd/.usda path (default: twin_hall_map.usd)")
    ap.add_argument("--gap", type=float, default=HALL_GAP,
                    help="width of the passage in the shared wall, in meters (default: %.1f)" % HALL_GAP)
    ap.add_argument("--assets-root", default=os.environ.get("ISAAC_ASSETS_ROOT", ASSET_ROOT_CDN),
                    help="Isaac asset root the warehouse props are referenced from")
    ap.add_argument("--no-assets", action="store_true",
                    help="skip the referenced Isaac props entirely (primitives only, fully offline)")
    ARGS = ap.parse_args()
    HALL_GAP = ARGS.gap
    if ARGS.no_assets:
        USE_ASSETS = False
    if os.path.exists(ARGS.output):
        os.remove(ARGS.output)
    stage = Usd.Stage.CreateNew(ARGS.output)

UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
UsdGeom.SetStageMetersPerUnit(stage, 1.0)

WORLD = "/World/TwinHall"
ROOT  = WORLD + "/Hall_A"               # one hall is built here, then copied to Hall_B
for old in [WORLD]:
    if stage.GetPrimAtPath(old):                                    # start clean
        stage.RemovePrim(old)
UsdGeom.Xform.Define(stage, "/World")
UsdGeom.Xform.Define(stage, WORLD)
for p in [ROOT, ROOT + "/Storage", ROOT + "/Shortcut",
          ROOT + "/Walls", ROOT + "/Columns", ROOT + "/Markings", ROOT + "/Ceiling",
          ROOT + "/Lights", ROOT + "/Assets", ROOT + "/Looks",
          ROOT + "/Products", ROOT + "/Handover", ROOT + "/Obstacle"]:
    UsdGeom.Xform.Define(stage, p)

# physics scene so colliders/rigid bodies act on Play (gravity down Z). Authored once,
# outside the hall subtree, so copying the hall does not give us a second one.
if USE_PHYSICS:
    scn = UsdPhysics.Scene.Define(stage, WORLD + "/PhysicsScene")
    scn.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    scn.CreateGravityMagnitudeAttr(9.81)


# ---- asset server (for referenced props) ---------------------------------
def get_assets_root():
    # a --assets-root / ISAAC_ASSETS_ROOT given on the command line wins; otherwise ask the
    # running Isaac Sim where its assets live; otherwise fall back to the public CDN copy.
    if ARGS is not None:
        return ARGS.assets_root
    for mod, fn in [("isaacsim.storage.native", "get_assets_root_path"),
                    ("omni.isaac.core.utils.nucleus", "get_assets_root_path"),
                    ("omni.isaac.nucleus", "get_assets_root_path")]:
        try:
            m = __import__(mod, fromlist=[fn])
            return getattr(m, fn)()
        except Exception:
            pass
    return ASSET_ROOT_CDN


asset_root = get_assets_root() if USE_ASSETS else ""
ASSETS_ON = bool(asset_root)                          # fall back to primitives if empty


def add_ref(path, url, pos, scale=1.0, rotz=0.0):
    wrapper = UsdGeom.Xform.Define(stage, path)
    wrapper.AddTranslateOp().Set(Gf.Vec3d(*pos))
    if rotz:
        wrapper.AddRotateZOp().Set(rotz)
    if scale != 1.0:
        wrapper.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
    child = stage.DefinePrim(path + "/Model", "Xform")
    child.GetReferences().AddReference(asset_root + url)


# ---- v5: semantic labels (for segmentation ground truth) -----------------
def add_semantic(prim, label):
    # tries the current Isaac Sim helper first, falls back to the raw Semantics schema API so
    # this still works on older installs. Both are best-effort: if neither import resolves,
    # the map still builds fine, just without labels.
    if not USE_SEMANTICS or prim is None:
        return
    try:
        from isaacsim.core.utils.semantics import add_labels
        add_labels(prim, labels=[label], instance_name="class")
        return
    except Exception:
        pass
    try:
        import Semantics
        api = Semantics.SemanticsAPI.Apply(prim, "Semantics")
        api.CreateSemanticTypeAttr().Set("class")
        api.CreateSemanticDataAttr().Set(label)
    except Exception:
        pass


# ---- v5: dynamic compound rigid body root ---------------------------------
def rigid_body_root(path, x, y, z, mass=1.0, rotz=0.0):
    # One parent Xform carries RigidBodyAPI + mass; children built under it (see the part
    # builder functions below) are collision-only shapes at LOCAL offsets, so the whole
    # assembled part moves/can-be-grasped as ONE dynamic body instead of independent statics.
    xf = UsdGeom.Xform.Define(stage, path)
    xfo = UsdGeom.Xformable(xf)
    xfo.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
    if rotz:
        xfo.AddRotateZOp().Set(rotz)
    if USE_PHYSICS:
        UsdPhysics.RigidBodyAPI.Apply(xf.GetPrim())
        UsdPhysics.MassAPI.Apply(xf.GetPrim()).CreateMassAttr(mass)
    return xf


# ---- materials -----------------------------------------------------------
# real OmniPBR (MDL) instead of flat UsdPreviewSurface. Checked against Isaac Sim's own
# built-in warehouse asset materials (local install, kit/mdl/core/Base/OmniPBR.mdl - ships with
# every Kit app, no Nucleus dependency) to see what actually makes them read as real instead of
# "Minecraft blocks": it isn't the color, it's round_edges_radius - every real manufactured part
# has a soft edge highlight instead of a perfectly sharp 90 degree corner, which is exactly what
# was missing here. Applying it once in this shared helper fixes every primitive in the map
# (stations, walls, crates, everything) in one place. Renders correctly under RTX (what the
# viewport already uses); note it will NOT render under the Storm (non-RTX) preview renderer.
def color_mat(name, color, rough=0.6, metallic=0.0, emissive=None, round_edges=0.006):
    path = ROOT + "/Looks/" + name
    mat = UsdShade.Material.Define(stage, path)
    sh = UsdShade.Shader.Define(stage, path + "/Shader")
    sh.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
    sh.SetSourceAsset(Sdf.AssetPath("OmniPBR.mdl"), "mdl")
    sh.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
    sh.CreateInput("diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    sh.CreateInput("reflection_roughness_constant", Sdf.ValueTypeNames.Float).Set(rough)
    sh.CreateInput("metallic_constant", Sdf.ValueTypeNames.Float).Set(metallic)
    sh.CreateInput("round_edges_radius", Sdf.ValueTypeNames.Float).Set(round_edges)
    sh.CreateInput("round_edges_across_materials", Sdf.ValueTypeNames.Bool).Set(True)
    if emissive is not None:
        # OmniPBR = tint(0-1) * intensity, vs. UsdPreviewSurface's single raw-added emissiveColor.
        # Reproduce the old (already-tuned, subdued) brightness exactly: intensity fixed at 1.0,
        # tint = the old tuple clamped to 0-1 - NOT renormalized/amplified. (An earlier version of
        # this multiplied intensity by peak*12, which blew every accent/marker out into neon - if
        # things look garish, that's the bug to look for again.)
        sh.CreateInput("enable_emission", Sdf.ValueTypeNames.Bool).Set(True)
        sh.CreateInput("emissive_color", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*(min(max(v, 0.0), 1.0) for v in emissive)))
        sh.CreateInput("emissive_intensity", Sdf.ValueTypeNames.Float).Set(1.0)
    sh.CreateOutput("out", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput("mdl").ConnectToSource(sh.ConnectableAPI(), "out")
    return mat


MAT = {
    "floor":    color_mat("floor",    (0.66, 0.64, 0.60), rough=0.22),            # warm polished concrete
    "aisle":    color_mat("aisle",    (0.74, 0.72, 0.68), rough=0.25),            # bright painted walkway
    "workzone": color_mat("workzone", (0.60, 0.57, 0.50), rough=0.35),           # sand-gray work zone
    "lane":     color_mat("lane",     (0.95, 0.78, 0.10), rough=0.4),            # safety-yellow lane line
    "shortcut": color_mat("shortcut", (0.66, 0.55, 0.34), rough=0.5),           # amber shortcut floor
    "step":     color_mat("step",     (0.95, 0.76, 0.10), rough=0.4),           # yellow hazard step
    "station":  color_mat("station",  (0.30, 0.34, 0.40), rough=0.4, metallic=0.3),
    "cabinet":  color_mat("cabinet",  (0.72, 0.74, 0.77), rough=0.35, metallic=0.2),  # machine housing
    "route":    color_mat("route",    (0.78, 0.12, 0.22), rough=0.4, emissive=(0.45, 0.05, 0.10)),  # painted shortcut route
    "storefl":  color_mat("storefl",  (0.63, 0.62, 0.58), rough=0.4),
    "shelf":    color_mat("shelf",    (0.44, 0.48, 0.55), rough=0.5, metallic=0.3),
    "wall":     color_mat("wall",     (0.82, 0.80, 0.75), rough=0.6),            # warm off-white panel
    "wainscot": color_mat("wainscot", (0.26, 0.37, 0.31), rough=0.5),           # deep green stripe
    "skirt":    color_mat("skirt",    (0.30, 0.28, 0.25), rough=0.7),           # dark warm skirting
    "column":   color_mat("column",   (0.45, 0.46, 0.43), rough=0.35, metallic=0.4),  # steel column
    "rail":     color_mat("rail",     (0.95, 0.76, 0.10), rough=0.4),           # safety rail
    "beam":     color_mat("beam",     (0.32, 0.34, 0.38), rough=0.6),           # ceiling truss
    "panel":    color_mat("panel",    (1.0, 1.0, 1.0), rough=0.9, emissive=(1.4, 1.42, 1.5)),  # light panel
    "armjoint": color_mat("armjoint", (0.22, 0.23, 0.26), rough=0.4, metallic=0.5),   # dark joints
    "crate":    color_mat("crate",    (0.74, 0.58, 0.36), rough=0.7),                 # cardboard crate
    "tote":     color_mat("tote",     (0.20, 0.34, 0.52), rough=0.45),                # blue plastic crate
    "totealt":  color_mat("totealt",  (0.38, 0.42, 0.46), rough=0.45),                # grey plastic crate
    "cratealt": color_mat("cratealt", (0.60, 0.47, 0.30), rough=0.7),                 # darker crate
    "slred":    color_mat("slred",    (0.85, 0.10, 0.10), rough=0.4, emissive=(0.9, 0.05, 0.05)),  # stack light
    "slamber":  color_mat("slamber",  (0.95, 0.70, 0.10), rough=0.4, emissive=(0.9, 0.55, 0.05)),
    "slgreen":  color_mat("slgreen",  (0.15, 0.80, 0.25), rough=0.4, emissive=(0.05, 0.7, 0.15)),
    "vac_body":    color_mat("vac_body",    (0.88, 0.89, 0.92), rough=0.35),                # finished puck body
    "vac_dark":    color_mat("vac_dark",    (0.16, 0.17, 0.20), rough=0.5),                 # trim/lid/bumper
    "vac_lidar":   color_mat("vac_lidar",   (0.07, 0.07, 0.09), rough=0.4, metallic=0.3),   # top lidar turret
    # year-2 mixed picking/packing bay materials
    "pickfl":   color_mat("pickfl",   (0.55, 0.56, 0.58), rough=0.45),                # picking-bay floor
    "lowshelf": color_mat("lowshelf", (0.40, 0.44, 0.50), rough=0.5, metallic=0.3),   # low shelf
    "goodsbox": color_mat("goodsbox", (0.78, 0.62, 0.40), rough=0.7),                 # rectangular carton
    "goodsalt": color_mat("goodsalt", (0.66, 0.50, 0.32), rough=0.7),                 # darker carton
    "can":      color_mat("can",      (0.80, 0.82, 0.85), rough=0.3, metallic=0.6),   # cylindrical can (metal)
    "canB":     color_mat("canB",     (0.72, 0.30, 0.24), rough=0.4, metallic=0.3),   # labelled can
    "pouch":    color_mat("pouch",    (0.85, 0.80, 0.55), rough=0.65),                # irregular pouch
    "pouchB":   color_mat("pouchB",   (0.55, 0.68, 0.78), rough=0.65),                # pouch alt
    # packing-station / handover / fault-stop demo materials
    "packfl":   color_mat("packfl",   (0.66, 0.68, 0.72), rough=0.3),                 # clean structured pack-zone floor
    "handover": color_mat("handover", (0.15, 0.65, 0.75), rough=0.3, emissive=(0.05, 0.28, 0.32)),  # handover boundary
    "detour":   color_mat("detour",   (0.95, 0.60, 0.08), rough=0.4, emissive=(0.5, 0.32, 0.03)),   # painted detour path
    "grout":    color_mat("grout",    (0.50, 0.52, 0.55), rough=0.35),                # subtle floor-tile grout line
    # v5: worn/traffic-marked floor patch - cheap way to break up a flat single-colour floor
    # without needing a real texture asset.
    "scuff":    color_mat("scuff",    (0.44, 0.45, 0.47), rough=0.32),
}

# ---- geometry helpers ----------------------------------------------------
def bind(prim, mat):
    UsdShade.MaterialBindingAPI.Apply(prim.GetPrim()).Bind(mat)


def box(path, size, center, mat, phys=None, mass=2.0, rotz=0.0):
    c = UsdGeom.Cube.Define(stage, path)
    c.GetSizeAttr().Set(1.0)
    x = UsdGeom.Xformable(c)
    x.AddTranslateOp().Set(Gf.Vec3d(*center))
    if rotz:                                        # for irregularly-piled clutter
        x.AddRotateZOp().Set(rotz)
    x.AddScaleOp().Set(Gf.Vec3f(*size))
    bind(c, mat)
    if USE_PHYSICS and phys:                       # "static" = collider only; "dynamic" = rigid body
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
        if phys == "dynamic":
            UsdPhysics.RigidBodyAPI.Apply(c.GetPrim())
            UsdPhysics.MassAPI.Apply(c.GetPrim()).CreateMassAttr(mass)
    return c


def cyl(path, radius, height, center, mat, axis="Z", phys=None, mass=1.0):
    # round primitive (discs, cans, shells, turrets)
    c = UsdGeom.Cylinder.Define(stage, path)
    c.CreateRadiusAttr(radius)
    c.CreateHeightAttr(height)
    c.CreateAxisAttr(axis)
    if axis == "X":
        ext = [Gf.Vec3f(-height / 2, -radius, -radius), Gf.Vec3f(height / 2, radius, radius)]
    elif axis == "Y":
        ext = [Gf.Vec3f(-radius, -height / 2, -radius), Gf.Vec3f(radius, height / 2, radius)]
    else:
        ext = [Gf.Vec3f(-radius, -radius, -height / 2), Gf.Vec3f(radius, radius, height / 2)]
    c.CreateExtentAttr(ext)
    UsdGeom.Xformable(c).AddTranslateOp().Set(Gf.Vec3d(*center))
    bind(c, mat)
    if USE_PHYSICS and phys:
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
        if phys == "dynamic":
            UsdPhysics.RigidBodyAPI.Apply(c.GetPrim())
            UsdPhysics.MassAPI.Apply(c.GetPrim()).CreateMassAttr(mass)
    return c


def stack_light(prefix, x, y, z0):
    # small red/amber/green status tower - iconic factory-line cue, makes each cell read as its own
    box(prefix + "_pole", (0.07, 0.07, 0.45), (x, y, z0 + 0.225), MAT["armjoint"])
    for k, m in enumerate(("slred", "slamber", "slgreen")):
        box(prefix + "_lamp%d" % k, (0.11, 0.11, 0.11), (x, y, z0 + 0.5 + k * 0.12), MAT[m])


def crate_stack(prefix, x, y, levels, size=(0.52, 0.42, 0.34)):
    # real rigid-body crates (stock parts) stacked on the floor, so storage/line look loaded.
    # v5: small seeded jitter per level so stacks don't read as a perfectly aligned CAD tower.
    mats = ("crate", "cratealt")
    for k in range(levels):
        z = 0.18 + k * (size[2] + 0.01)
        jx = x + RNG.uniform(-0.03, 0.03)
        jy = y + RNG.uniform(-0.03, 0.03)
        jr = RNG.uniform(-6.0, 6.0)
        c = box("%s_%d" % (prefix, k), size, (jx, jy, z), MAT[mats[k % 2]], phys="dynamic", mass=1.5, rotz=jr)
        add_semantic(c.GetPrim(), "crate")


def plastic_crate(prefix, x, y, z, mat, w=0.5, d=0.38, h=0.3, rotz=0.0):
    # An open-top plastic crate: a floor plus four thin walls, authored as flat sibling
    # prims (no wrapper Xform). randomize_map.py keeps the walls together by name.
    t = 0.022
    ca, sa = math.cos(math.radians(rotz)), math.sin(math.radians(rotz))

    def put(nm, size, lx, ly, lz):
        box(prefix + "_" + nm, size, (x + lx * ca - ly * sa, y + lx * sa + ly * ca, z + lz),
            mat, phys="static", rotz=rotz)

    put("base",  (w, d, t), 0.0, 0.0, t / 2.0)
    put("wallN", (w, t, h), 0.0,  d / 2.0 - t / 2.0, h / 2.0)
    put("wallS", (w, t, h), 0.0, -d / 2.0 + t / 2.0, h / 2.0)
    put("wallE", (t, d, h),  w / 2.0 - t / 2.0, 0.0, h / 2.0)
    put("wallW", (t, d, h), -w / 2.0 + t / 2.0, 0.0, h / 2.0)


def pallet_rack(prefix, x, y, bays=2, levels=3, axis="y", bay_w=2.1, depth=1.1, lift=1.0):
    # A real standing pallet rack, built from primitives: uprights at every bay boundary,
    # a pair of beams and a deck plate per level. Built here rather than referenced from the
    # warehouse asset library because the deck heights have to be known to stack cargo on
    # them (and because a primitive rack loads with no asset server).
    run = bays * bay_w
    post_h = levels * lift + 0.62   # uprights stand clear of the top pallet

    def place(along, across, z, size_along, size_across, size_z, mat, phys=None):
        # 'along' runs down the rack, 'across' is its depth
        if axis == "y":
            box(prefix + "_%s" % place.tag, (size_across, size_along, size_z),
                (x + across, y + along, z), mat, phys=phys)
        else:
            box(prefix + "_%s" % place.tag, (size_along, size_across, size_z),
                (x + along, y + across, z), mat, phys=phys)

    for i in range(bays + 1):                                   # uprights
        a = -run / 2.0 + i * bay_w
        for e, c in enumerate((-depth / 2.0 + 0.06, depth / 2.0 - 0.06)):
            place.tag = "post%d_%d" % (i, e)
            place(a, c, post_h / 2.0, 0.09, 0.09, post_h, MAT["shelf"], phys="static")
    for k in range(levels):                                     # beams + deck per level
        z = (k + 1) * lift
        for e, c in enumerate((-depth / 2.0 + 0.06, depth / 2.0 - 0.06)):
            place.tag = "beam%d_%d" % (k, e)
            place(0.0, c, z, run, 0.10, 0.12, MAT["rail"])
        place.tag = "deck%d" % k
        place(0.0, 0.0, z - 0.03, run - 0.02, depth - 0.16, 0.04, MAT["shelf"], phys="static")
    return run, post_h


def rack_cargo(prefix, x, y, bays=2, levels=3, axis="y", bay_w=2.1, lift=1.0, seed_shift=0):
    # pallets of boxes and plastic crates sitting on the rack decks and on the floor under
    # them. Static: cargo that is meant to stay put on a shelf, not to be pushed around.
    run = bays * bay_w
    carton = ("goodsbox", "crate", "goodsalt", "cratealt")
    plastic = ("tote", "totealt")
    for k in range(levels + 1):                                 # level 0 = the floor bay
        z0 = 0.0 if k == 0 else k * lift + 0.02
        for b in range(bays):
            if (b + k + seed_shift) % 4 == 3:                   # leave the odd bay empty
                continue
            a = -run / 2.0 + (b + 0.5) * bay_w
            for j in range(2):
                da = (j - 0.5) * 0.80 + RNG.uniform(-0.04, 0.04)
                jitter = RNG.uniform(-0.04, 0.04)
                rz = RNG.uniform(-4.0, 4.0)
                name = "%s_%d_%d_%d" % (prefix, k, b, j)
                px = x + (jitter if axis == "y" else a + da)
                py = y + (a + da if axis == "y" else jitter)
                if (b + k + j + seed_shift) % 3 == 1:           # plastic crate
                    m = MAT[plastic[(b + j + seed_shift) % 2]]
                    w_, d_ = (0.38, 0.5) if axis == "y" else (0.5, 0.38)
                    plastic_crate(name, px, py, z0, m, w=w_, d=d_, h=0.3, rotz=rz)
                else:                                            # cardboard carton
                    m = MAT[carton[(b + k + j + seed_shift) % 4]]
                    h_ = RNG.choice((0.34, 0.42, 0.5))
                    size = (0.52, 0.62, h_) if axis == "y" else (0.62, 0.52, h_)
                    box(name, size, (px, py, z0 + h_ / 2.0), m, phys="static", rotz=rz)



def tile_grid(prefix, cx_, cy_, w, d, top_z, spacing=1.4, line_w=0.03):
    # subtle grout-line grid over a floor rect, purely primitive (no texture asset needed).
    # Sits just above the given surface's top so it reads as tile joints, not a new floor layer.
    nx = int(w // spacing)
    for i in range(1, nx + 1):
        x = cx_ - w / 2.0 + i * spacing
        if x >= cx_ + w / 2.0 - 0.05:
            break
        box("%s_v%d" % (prefix, i), (line_w, d, 0.004), (x, cy_, top_z), MAT["grout"])
    ny = int(d // spacing)
    for j in range(1, ny + 1):
        y = cy_ - d / 2.0 + j * spacing
        if y >= cy_ + d / 2.0 - 0.05:
            break
        box("%s_h%d" % (prefix, j), (w, line_w, 0.004), (cx_, y, top_z), MAT["grout"])


def scuff(prefix, cx_, cy_, w, d, top_z, n=3):
    # v5: a few irregular darker patches mimicking foot/wheel traffic wear - purely cosmetic,
    # breaks up the flat single-colour floor without needing a real texture asset.
    for i in range(n):
        sx_ = cx_ + RNG.uniform(-w / 2.0, w / 2.0)
        sy_ = cy_ + RNG.uniform(-d / 2.0, d / 2.0)
        sw, sd = RNG.uniform(0.3, 0.7), RNG.uniform(0.2, 0.5)
        box("%s_%d" % (prefix, i), (sw, sd, 0.006), (sx_, sy_, top_z + 0.003), MAT["scuff"],
            rotz=RNG.uniform(0.0, 180.0))


def hazard_stripe(prefix, cx_, cy_, w, d, top_z, n=3):
    # diagonal caution stripes over a step surface (real crossing hazard for the fault/step demo).
    for i in range(n):
        t = (i + 0.5) / n - 0.5
        sx_ = cx_ + t * w
        box("%s_%d" % (prefix, i), (0.10, d * 1.15, 0.01), (sx_, cy_, top_z + 0.006), MAT["armjoint"], rotz=35)


def stalled_unit(prefix, x, y):
    # stylized broken-down mobile-robot placeholder (generic blocky silhouette, no real USD
    # needed). The base is a real static collider, so the aisle is
    # actually physically blocked, not just painted around. Low flat base + a stalled payload
    # stack on top (reads as "was mid-delivery when it stopped").
    base = box(prefix + "_base",   (0.65, 0.55, 0.18), (x, y, 0.09),  MAT["armjoint"], phys="static")
    add_semantic(base.GetPrim(), "faulty_robot")
    box(prefix + "_torso",  (0.35, 0.35, 0.50), (x, y, 0.43),  MAT["cabinet"])   # stalled payload/torso stack
    cyl(prefix + "_bpole",  0.03, 0.25,         (x, y, 0.805), MAT["armjoint"])
    box(prefix + "_beacon", (0.12, 0.12, 0.12), (x, y, 0.99),  MAT["slamber"])   # amber warning beacon
    box(prefix + "_warn",   (0.9, 0.9, 0.02),   (x, y, 0.021), MAT["route"], rotz=45)  # hazard floor diamond
    # spilled load next to the stalled robot - it was mid-delivery, not just idle
    box(prefix + "_drop",   (0.42, 0.34, 0.30), (x - 0.62, y + 0.40, 0.15),  MAT["cratealt"],
        phys="dynamic", mass=1.2, rotz=35)
    box(prefix + "_bit0",   (0.12, 0.10, 0.08), (x - 0.85, y + 0.15, 0.04),  MAT["goodsbox"],
        phys="dynamic", mass=0.3, rotz=60)
    box(prefix + "_bit1",   (0.10, 0.09, 0.07), (x - 0.40, y + 0.62, 0.035), MAT["goodsalt"],
        phys="dynamic", mass=0.3, rotz=-20)


# ---- a finished round product puck, staged as loose goods on the storage floor.
# Local origin = the surface it rests on; the world pose/physics come from
# rigid_body_root() wrapping the call, so it is one graspable dynamic rigid body.
def finished_unit(path):            # a finished round unit, staged as loose goods
    cyl(path + "/body",   0.17, 0.075, (0, 0, 0.0375), MAT["vac_body"], phys="static")
    cyl(path + "/lid",    0.165, 0.012, (0, 0, 0.081), MAT["vac_dark"])
    cyl(path + "/lidar",  0.045, 0.028, (0.02, 0, 0.10), MAT["vac_lidar"])
    box(path + "/bumper", (0.02, 0.22, 0.05), (0.16, 0, 0.03), MAT["vac_dark"])  # front bumper hint


# ---- layout ----
n = CELL_COUNT
line_len = (n - 1) * CELL_SPACING
cx = line_len / 2.0
aisle_cy = CELL_Y - 1.3 - CORRIDOR_WIDTH / 2.0
aisle_len = line_len + 2.0 * AISLE_RUNWAY
aisle_left  = cx - aisle_len / 2.0
aisle_right = cx + aisle_len / 2.0
store_w, store_d = 3.2, 4.4
store_x = aisle_right + store_w / 2.0 + 0.6           # storage just past the aisle's right end
# Pulled up against the north wall instead of sitting on the aisle centre line: the east end
# of the hall is where the next hall is joined, and a rack row centred on the aisle would
# stand straight in front of the passage.
store_cy = SHORTCUT_Y + SHORTCUT_WIDTH / 2.0 - store_d / 2.0

# year-2 picking/packing bay sits south of the main aisle, west side
pick_w, pick_d = 6.5, 4.0
pick_x  = aisle_left + 1.5 + pick_w / 2.0
pick_top = aisle_cy - CORRIDOR_WIDTH / 2.0 - 0.9     # south edge of aisle, minus a gap
pick_cy = pick_top - pick_d / 2.0

# the pack zone sits east of the picking bay, same row, separated by the handover strip
pack_d = pick_d                                       # same depth so the two zones align in y
pack_x = pick_x + pick_w / 2.0 + BOUND_W + PACK_W / 2.0
pack_cy = pick_cy
hand_x = pick_x + pick_w / 2.0 + BOUND_W / 2.0        # boundary strip center (between the two zones)

# walls enclose everything (aisle runway + storage + back shortcut + pick/pack bay) with a margin
gx0 = min(aisle_left, -SHORTCUT_WIDTH / 2.0 - 1.5) - 1.5
# the east side is where the next hall is joined, so it gets a wider margin: that margin is
# the lane a robot arrives into after coming through the passage, and 1.5 m of it would put
# the doorway right against the back of the storage racks.
gx1 = max(aisle_right, store_x + store_w / 2.0) + JOIN_MARGIN
gy0 = aisle_cy - CORRIDOR_WIDTH / 2.0 - 1.3
if USE_PICKZONE:
    gy0 = min(gy0, pick_cy - pick_d / 2.0 - 1.0)     # extend the south wall to enclose the bay
    if USE_PACKSTATION:
        gx1 = max(gx1, pack_x + PACK_W / 2.0 + 1.5)  # extend east wall if the pack zone needs it
gy1 = SHORTCUT_Y + SHORTCUT_WIDTH / 2.0 + 0.6
midx, midy = (gx0 + gx1) / 2.0, (gy0 + gy1) / 2.0

# ---- floor + painted zones ----
floor_prim = box(ROOT + "/Floor", (gx1 - gx0, gy1 - gy0, 0.1), (midx, midy, -0.05), MAT["floor"], phys="static")
add_semantic(floor_prim.GetPrim(), "floor")
box(ROOT + "/MainCorridor", (aisle_len, CORRIDOR_WIDTH, 0.02), (cx, aisle_cy, 0.011), MAT["aisle"])
tile_grid(ROOT + "/Markings/Tile_aisle", cx, aisle_cy, aisle_len, CORRIDOR_WIDTH, 0.023)
box(ROOT + "/Markings/LineZone", (line_len + 2.4, 2.0, 0.015), (cx, CELL_Y, 0.012), MAT["workzone"])
tile_grid(ROOT + "/Markings/Tile_line", cx, CELL_Y, line_len + 2.4, 2.0, 0.021)
for e, edge in enumerate((aisle_cy - CORRIDOR_WIDTH / 2.0 + 0.12, aisle_cy + CORRIDOR_WIDTH / 2.0 - 0.12)):
    box(ROOT + "/Markings/Lane_%d" % e, (aisle_len, 0.12, 0.03), (cx, edge, 0.02), MAT["lane"])
# apron: painted floor east of the aisle - the run from the aisle to the storage zone and on
# to the passage in the shared wall, so it reads as one continuous walkway.
aisle_right = cx + aisle_len / 2.0
store_left  = store_x - store_w / 2.0
if store_left > aisle_right:
    box(ROOT + "/Storage/Apron", (store_left - aisle_right, CORRIDOR_WIDTH, 0.02),
        ((aisle_right + store_left) / 2.0, aisle_cy, 0.011), MAT["aisle"])
    box(ROOT + "/Storage/ApronN", (store_left - aisle_right, store_cy - aisle_cy, 0.02),
        ((aisle_right + store_left) / 2.0, (aisle_cy + store_cy) / 2.0, 0.011), MAT["aisle"])

# v5: a little floor wear where feet/wheels actually concentrate - main aisle centerline and
# the apron into storage. Purely cosmetic (no texture asset needed, see scuff()).
scuff(ROOT + "/Markings/Scuff_aisle", cx, aisle_cy, aisle_len * 0.9, CORRIDOR_WIDTH * 0.5, 0.023, n=6)
scuff(ROOT + "/Markings/Scuff_apron", (aisle_right + store_left) / 2.0, aisle_cy, 1.0, CORRIDOR_WIDTH * 0.6, 0.012, n=2)

# ---- back shortcut: a narrow L-path behind the work zone ----
# physically narrow (back wall + front guard) so a wide mobile base cannot enter, and gated by a
# low step a wheeled base cannot climb. A left connector drops from the back leg to the main aisle.
conn_x  = aisle_left + 1.0                        # connector near the aisle's left end
back_x0 = conn_x - SHORTCUT_WIDTH / 2.0
back_x1 = line_len + CELL_SPACING * 0.5           # runs the length of the work zone
back_cx = (back_x0 + back_x1) / 2.0
box(ROOT + "/Shortcut/Back", (back_x1 - back_x0, SHORTCUT_WIDTH, 0.02),
    (back_cx, SHORTCUT_Y, 0.012), MAT["shortcut"])
conn_lo = aisle_cy + CORRIDOR_WIDTH / 2.0            # meets the far edge of the main aisle
conn_hi = SHORTCUT_Y + SHORTCUT_WIDTH / 2.0          # meets the back leg
box(ROOT + "/Shortcut/Connector", (SHORTCUT_WIDTH, conn_hi - conn_lo, 0.02),
    (conn_x, (conn_lo + conn_hi) / 2.0, 0.012), MAT["shortcut"])
# low step where the shortcut leaves the aisle (the gate that blocks a wheeled base)
box(ROOT + "/Shortcut/StepEntry", (SHORTCUT_WIDTH, 0.32, STEP_HEIGHT),
    (conn_x, conn_lo + 0.5, STEP_HEIGHT / 2.0), MAT["step"], phys="static")
hazard_stripe(ROOT + "/Shortcut/StepEntry_hz", conn_x, conn_lo + 0.5, SHORTCUT_WIDTH, 0.32, STEP_HEIGHT)
sc_front = SHORTCUT_Y - SHORTCUT_WIDTH / 2.0
box(ROOT + "/Shortcut/StepMid", (0.35, gy1 - sc_front, STEP_HEIGHT + 0.03),
    (back_cx, (sc_front + gy1) / 2.0, (STEP_HEIGHT + 0.03) / 2.0), MAT["step"], phys="static")
# front guard makes the back leg physically narrow (gap at left = the connector entry)
guard_lo, guard_hi = conn_x + SHORTCUT_WIDTH, back_x1
box(ROOT + "/Shortcut/Guard", (guard_hi - guard_lo, 0.12, 0.6),
    ((guard_lo + guard_hi) / 2.0, sc_front, 0.30), MAT["wainscot"], phys="static")

# painted route: main aisle -> up the connector -> along the back leg, so the shortcut reads
# as connected to the main aisle instead of a detached strip behind the work zone.
def dashed_line(prefix, p0, p1, mat, dash=0.35, gap=0.25, w=0.10, z=0.03):
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    horiz = abs(ux) >= abs(uy)
    d, k = 0.0, 0
    while d < L:
        seg = min(dash, L - d)
        mx, my = x0 + ux * (d + seg / 2.0), y0 + uy * (d + seg / 2.0)
        size = (seg, w, 0.02) if horiz else (w, seg, 0.02)
        box("%s_%d" % (prefix, k), size, (mx, my, z), mat)
        d += dash + gap; k += 1
dashed_line(ROOT + "/Markings/RouteConn", (conn_x, aisle_cy), (conn_x, SHORTCUT_Y), MAT["route"])
dashed_line(ROOT + "/Markings/RouteBack", (conn_x, SHORTCUT_Y), (back_x1 - 0.4, SHORTCUT_Y), MAT["route"])

# ---- a stalled unit blocking the main aisle, with dashed detour markings around it. It is a
# real static collider, so a planner has to route around it rather than through it. ----
if USE_OBSTACLE:
    stall_x = cx + 2.5                                # kept off the aisle centre so a robot can
                                                      # still be spawned at the middle of the aisle
    stalled_unit(ROOT + "/Obstacle/StalledUnit", stall_x, aisle_cy)
    bypass_y = aisle_cy + CORRIDOR_WIDTH / 2.0 - 0.8   # detour along the far edge of the aisle
    dashed_line(ROOT + "/Markings/Detour_0", (stall_x - 2.2, aisle_cy), (stall_x, bypass_y), MAT["detour"])
    dashed_line(ROOT + "/Markings/Detour_1", (stall_x, bypass_y), (stall_x + 2.2, aisle_cy), MAT["detour"])

# ---- perimeter walls: panel + wainscot + dark skirting ----
# The hall is closed on all four sides here. The two walls that end up back to back at the
# seam (Hall A's east, Hall B's west) are deleted again in the twin-hall block at the end of
# the file and replaced by one shared wall with the passage in it.
WALL_T = 0.15
for nm, size, ctr in [
    ("North", (gx1 - gx0, WALL_T, WALL_H), (midx, gy1, WALL_H / 2.0)),
    ("South", (gx1 - gx0, WALL_T, WALL_H), (midx, gy0, WALL_H / 2.0)),
    ("West",  (WALL_T, gy1 - gy0, WALL_H), (gx0, midy, WALL_H / 2.0)),
    ("East",  (WALL_T, gy1 - gy0, WALL_H), (gx1, midy, WALL_H / 2.0)),
]:
    wall_prim = box(ROOT + "/Walls/" + nm, size, ctr, MAT["wall"], phys="static")
    add_semantic(wall_prim.GetPrim(), "wall")
    box(ROOT + "/Walls/" + nm + "_wainscot", (size[0] + 0.02, size[1] + 0.02, 0.5),
        (ctr[0], ctr[1], 1.05), MAT["wainscot"])
    box(ROOT + "/Walls/" + nm + "_skirt", (size[0] + 0.04, size[1] + 0.04, 0.18),
        (ctr[0], ctr[1], 0.09), MAT["skirt"])

# ---- structural columns hugging the long walls ----
col_xs = [gx0 + 1.2, cx - 2.5, cx + 2.5, gx1 - 1.2]
for i, cxp in enumerate(col_xs):
    for edge, yy in (("S", gy0 + 0.45), ("N", gy1 - 0.45)):
        box(ROOT + "/Columns/Col_%s%d" % (edge, i), (0.45, 0.45, WALL_H),
            (cxp, yy, WALL_H / 2.0), MAT["column"], phys="static")

# ---- overhead: light panels + thin cross truss beams + v5 cable trays (off by default so the
# top is open) ----
if SHOW_CEILING:
    panel_z = WALL_H - 0.05
    for j, ry in enumerate((aisle_cy, CELL_Y)):
        box(ROOT + "/Ceiling/Panel_%d" % j, (aisle_len, 0.7, 0.08), (cx, ry, panel_z), MAT["panel"])
    for k in range(5):
        bx = gx0 + (k + 0.5) * (gx1 - gx0) / 5.0
        box(ROOT + "/Ceiling/Beam_%d" % k, (0.18, gy1 - gy0, 0.14), (bx, midy, panel_z + 0.05), MAT["beam"])
    # v5: cable trays hung a bit below the truss beams - overhead clutter reads more like a real
    # industrial hall than a bare lit ceiling.
    tray_z = panel_z - 0.35
    for j, ry in enumerate((aisle_cy - 1.0, CELL_Y + 1.0)):
        box(ROOT + "/Ceiling/CableTray_%d" % j, (aisle_len, 0.25, 0.10), (cx, ry, tray_z), MAT["beam"])

# ---- the hall centre is left empty on purpose: the painted work zone above marks
# where an assembly line would stand, but nothing is built on it, so the floor is
# clear for whatever the user drives/spawns in. ----

# ---- aisle safety bollards (short yellow posts along the walkway edge) ----
for i in range(n):
    box(ROOT + "/Markings/Bollard_%d" % i, (0.12, 0.12, 0.5),
        (i * CELL_SPACING, aisle_cy - CORRIDOR_WIDTH / 2.0 + 0.3, 0.25), MAT["rail"], phys="static")

# ---- crate stacks: real rigid bodies, stacked in storage and staged along the aisle ----
crate_stack(ROOT + "/Storage/Stock0", store_x - 2.4, store_cy + 1.4, 3)
crate_stack(ROOT + "/Storage/Stock1", store_x - 2.4, store_cy - 1.3, 2)
crate_stack(ROOT + "/Storage/Stage0", 2.0, aisle_cy + 2.1, 2)         # staged, west end
crate_stack(ROOT + "/Storage/Stage1", 11.0, aisle_cy - 2.1, 2)        # staged, east end

# ---- loose finished units staged on the storage floor (rigid bodies, pickable) ----
if USE_PRODUCTS:
    finished = [(store_x - 3.0, store_cy + 0.5), (store_x - 3.3, store_cy - 0.2),
                (store_x - 3.0, store_cy - 0.9), (aisle_left + 1.6, CELL_Y - 0.2)]
    for k, (vx, vy) in enumerate(finished):
        vpath = ROOT + "/Products/Vac_%d" % k
        vroot = rigid_body_root(vpath, vx, vy, 0.0, mass=0.65)
        finished_unit(vpath)
        add_semantic(vroot.GetPrim(), "finished_unit")

# ---- storage zone: floor + two back-to-back pallet rack rows, loaded with cargo ----
box(ROOT + "/Storage/Floor", (store_w, store_d, 0.02), (store_x, store_cy, 0.011), MAT["storefl"])
for j, sx_ in enumerate((-1.0, 1.0)):
    rp = ROOT + "/Storage/Rack_%d" % j
    pallet_rack(rp, store_x + sx_, store_cy, bays=2, levels=3, axis="y")
    add_semantic(stage.GetPrimAtPath(rp + "_post0_0"), "storage_rack")
    rack_cargo(ROOT + "/Storage/RackLoad_%d" % j, store_x + sx_, store_cy,
               bays=2, levels=3, axis="y", seed_shift=j)

# a second rack stands in the far corner, away from the aisle and well clear of the passage
corner_rack_x, corner_rack_y = gx1 - 4.0, gy0 + 0.95
pallet_rack(ROOT + "/Storage/RackCorner", corner_rack_x, corner_rack_y,
            bays=2, levels=3, axis="x")
add_semantic(stage.GetPrimAtPath(ROOT + "/Storage/RackCorner_post0_0"), "storage_rack")
rack_cargo(ROOT + "/Storage/RackCornerLoad", corner_rack_x, corner_rack_y,
           bays=2, levels=3, axis="x", seed_shift=2)

if ASSETS_ON:
    clutter = [
        (PALLET, (store_x - 3.2, aisle_cy + 1.6, 0.0),  0),
        (PALLET, (store_x - 3.2, aisle_cy - 1.6, 0.0),  0),
        (BOXA,   (store_x - 3.2, aisle_cy + 1.6, 0.18), 0),
        (BOXB,   (store_x - 3.2, aisle_cy - 1.6, 0.18), 20),
        (BOXC,   (1.5,           aisle_cy - 1.2, 0.18), -15),
        (BOXA,   (5.0,           aisle_cy + 1.0, 0.18), 35),
    ]
    for k, (url, pos, rz) in enumerate(clutter):
        add_ref(ROOT + "/Assets/Clutter_%d" % k, url, pos, 1.0, rz)

# ---- extra props (scale/orientation are eyeballed - toggle with USE_EXTRA) ----
# trolleys, wall props (extinguisher/fuse box), standing aisle signs, and plastic crates.
if ASSETS_ON and USE_EXTRA:
    extras = [
        # (url, position, rotZ) - positions are first-pass; adjust in the viewport.
        # kept off the aisle centre line and clear of the stalled unit.
        (PUSHCART,  (0.0, aisle_cy + 1.8, 0.0),          90),   # trolley, north edge
        (PUSHCART,  (3.0, aisle_cy - 1.6, 0.0),          90),   # trolley, south edge
        (EXTING,    (gx0 + 2.0, gy0 + 0.25, 0.0),         0),   # floor-standing at south wall
        (FUSEBOX,   (gx0 + 2.8, gy0 + 0.20, 1.20),        0),   # wall-mounted (raise/lower to fit)
        (AISLESIGN, (-1.5, aisle_cy - 2.0, 0.0),          0),   # sign at aisle entry (west)
        (AISLESIGN, (aisle_right + 0.3, aisle_cy - 2.9, 0.0),  0),  # sign at the aisle's east end
        (CRATE,     (store_x - 1.2, store_cy + 1.4, 0.0), 0),   # storage crates
        (CRATE,     (store_x - 1.2, store_cy + 1.4, 0.45), 15),
        (CRATE,     (store_x - 1.2, store_cy - 1.4, 0.0), -10),
    ]
    for k, (url, pos, rz) in enumerate(extras):
        add_ref(ROOT + "/Assets/Extra_%d" % k, url, pos, 1.0, rz)

# ---- mixed picking bay south of the aisle ----
# goods piled irregularly on the floor + low shelves (rectangular cartons / cylindrical cans /
# irregular pouches), with a narrow pick aisle down the middle. Deliberately cluttered: it is
# the hard case for navigation/perception, as opposed to the clean main aisle.
if USE_PICKZONE:
    P = ROOT + "/PickZone"
    UsdGeom.Xform.Define(stage, P)
    box(P + "/Floor", (pick_w, pick_d, 0.02), (pick_x, pick_cy, 0.012), MAT["pickfl"])
    tile_grid(P + "/Tile", pick_x, pick_cy, pick_w, pick_d, 0.024)
    # short access strip linking the main aisle to the bay
    acc_lo = pick_cy + pick_d / 2.0
    acc_hi = aisle_cy - CORRIDOR_WIDTH / 2.0
    if acc_hi > acc_lo:
        box(P + "/Access", (2.0, acc_hi - acc_lo, 0.015), (pick_x, (acc_lo + acc_hi) / 2.0, 0.012), MAT["aisle"])
    # narrow pick aisle down the middle, yellow edge lines
    box(P + "/Lane", (pick_w - 0.4, 0.9, 0.015), (pick_x, pick_cy + 0.1, 0.02), MAT["aisle"])
    for e, ey in enumerate((pick_cy + 0.1 - 0.45, pick_cy + 0.1 + 0.45)):
        box(P + "/LaneEdge_%d" % e, (pick_w - 0.4, 0.06, 0.025), (pick_x, ey, 0.022), MAT["lane"])

    # low shelves along the bay's south wall
    def low_shelf(prefix, x, y, w=1.9, d=0.55):
        d0 = box(prefix + "_deck0", (w, d, 0.05), (x, y, 0.06), MAT["lowshelf"], phys="static")
        add_semantic(d0.GetPrim(), "low_shelf")
        box(prefix + "_deck1", (w, d, 0.05), (x, y, 0.42), MAT["lowshelf"], phys="static")
        box(prefix + "_back",  (w, 0.04, 0.44), (x, y + d / 2.0, 0.22), MAT["lowshelf"], phys="static")
    shelf_y = pick_cy - pick_d / 2.0 + 0.45
    low_shelf(P + "/Shelf0", pick_x - 1.5, shelf_y)
    low_shelf(P + "/Shelf1", pick_x + 1.5, shelf_y)

    # cylindrical-can helper (upright, or tipped on its side via axis)
    def can(prefix, x, y, z0, mat, r=0.05, h=0.13, axis="Z"):
        if axis == "Z":
            return cyl(prefix, r, h, (x, y, z0 + h / 2.0), mat, axis="Z", phys="static")
        else:
            return cyl(prefix, r, h, (x, y, z0 + r), mat, axis=axis, phys="static")

    # goods on the low shelves (both decks) - irregular
    g = box(P + "/g_box0", (0.34, 0.28, 0.22), (pick_x - 1.9, shelf_y, 0.20),        MAT["goodsbox"], phys="static", rotz=8)
    add_semantic(g.GetPrim(), "goods_box")
    g = box(P + "/g_box1", (0.30, 0.24, 0.20), (pick_x - 1.1, shelf_y - 0.03, 0.19), MAT["goodsalt"], phys="static", rotz=-12)
    add_semantic(g.GetPrim(), "goods_box")
    c = can(P + "/g_can0", pick_x - 1.5, shelf_y + 0.05, 0.085, MAT["can"])
    add_semantic(c.GetPrim(), "goods_can")
    c = can(P + "/g_can1", pick_x - 1.35, shelf_y - 0.06, 0.085, MAT["canB"])
    add_semantic(c.GetPrim(), "goods_can")
    g = box(P + "/g_pch0", (0.30, 0.22, 0.07), (pick_x + 1.2, shelf_y, 0.475),        MAT["pouch"], phys="static", rotz=15)
    add_semantic(g.GetPrim(), "goods_pouch")
    g = box(P + "/g_pch1", (0.26, 0.20, 0.06), (pick_x + 1.7, shelf_y - 0.04, 0.47),  MAT["pouchB"], phys="static", rotz=-20)
    add_semantic(g.GetPrim(), "goods_pouch")
    g = box(P + "/g_box2", (0.28, 0.22, 0.18), (pick_x + 2.3, shelf_y + 0.02, 0.51),  MAT["goodsbox"], phys="static", rotz=6)
    add_semantic(g.GetPrim(), "goods_box")

    # goods piled on the floor toward the aisle (north band) - some leaning/stacked/tipped
    floor_y = pick_cy + pick_d / 2.0 - 0.55
    g = box(P + "/f_box0", (0.40, 0.32, 0.28), (pick_x - 2.2, floor_y, 0.16),         MAT["goodsbox"], phys="static", rotz=-10)
    add_semantic(g.GetPrim(), "goods_box")
    g = box(P + "/f_box1", (0.34, 0.30, 0.24), (pick_x - 2.15, floor_y - 0.05, 0.44), MAT["goodsalt"], phys="static", rotz=18)   # stacked
    add_semantic(g.GetPrim(), "goods_box")
    g = box(P + "/f_box2", (0.44, 0.30, 0.26), (pick_x - 1.2, floor_y + 0.1, 0.15),   MAT["goodsalt"], phys="static", rotz=22)
    add_semantic(g.GetPrim(), "goods_box")
    c = can(P + "/f_can0", pick_x - 0.5, floor_y, 0.0, MAT["canB"], h=0.15)
    add_semantic(c.GetPrim(), "goods_can")
    c = can(P + "/f_can1", pick_x - 0.35, floor_y - 0.12, 0.0, MAT["can"], h=0.15)
    add_semantic(c.GetPrim(), "goods_can")
    c = can(P + "/f_can2", pick_x - 0.62, floor_y + 0.1, 0.0, MAT["can"], h=0.15)
    add_semantic(c.GetPrim(), "goods_can")
    c = can(P + "/f_can3", pick_x + 0.2, floor_y + 0.05, 0.0, MAT["canB"], r=0.055, h=0.11, axis="Y")   # tipped over
    add_semantic(c.GetPrim(), "goods_can")
    g = box(P + "/f_pch0", (0.34, 0.24, 0.06), (pick_x + 0.9, floor_y, 0.05),         MAT["pouch"], phys="static", rotz=-25)
    add_semantic(g.GetPrim(), "goods_pouch")
    g = box(P + "/f_pch1", (0.30, 0.22, 0.05), (pick_x + 1.4, floor_y + 0.08, 0.045), MAT["pouchB"], phys="static", rotz=30)
    add_semantic(g.GetPrim(), "goods_pouch")
    g = box(P + "/f_box3", (0.38, 0.28, 0.24), (pick_x + 2.0, floor_y, 0.15),         MAT["goodsbox"], phys="static", rotz=-8)
    add_semantic(g.GetPrim(), "goods_box")
    g = box(P + "/f_pch2", (0.28, 0.20, 0.05), (pick_x + 2.2, floor_y - 0.1, 0.28),   MAT["pouch"], phys="static", rotz=12)     # draped on the box
    add_semantic(g.GetPrim(), "goods_pouch")

    # ---- pack zone + handover strip ----
    # A clean zone east of the cluttered picking bay, separated by a marked boundary strip:
    # the strip + round pad mark where a pick is handed over before it is packed.
    if USE_PACKSTATION:
        # handover boundary: distinct-coloured strip + a round pad marking the exact handoff point
        box(ROOT + "/Handover/Boundary", (BOUND_W, pack_d, 0.025), (hand_x, pack_cy, 0.0135), MAT["handover"])
        cyl(ROOT + "/Handover/Pad", 0.4, 0.03, (hand_x, pack_cy, 0.028), MAT["handover"])

        P2 = ROOT + "/PackZone"
        UsdGeom.Xform.Define(stage, P2)
        box(P2 + "/Floor", (PACK_W, pack_d, 0.02), (pack_x, pack_cy, 0.012), MAT["packfl"])
        tile_grid(P2 + "/Tile", pack_x, pack_cy, PACK_W, pack_d, 0.024)
        # access apron linking the pack zone to the main aisle (mirrors the pick bay's own strip)
        acc2_lo = pack_cy + pack_d / 2.0
        acc2_hi = aisle_cy - CORRIDOR_WIDTH / 2.0
        if acc2_hi > acc2_lo:
            box(P2 + "/Access", (2.0, acc2_hi - acc2_lo, 0.015),
                (pack_x, (acc2_lo + acc2_hi) / 2.0, 0.012), MAT["aisle"])
        # packing table, close to the boundary so the handover-to-pack move is short
        table_x = pack_x - PACK_W / 2.0 + 0.8
        table_base = box(P2 + "/Table_base", (0.9, 0.6, 0.75), (table_x, pack_cy, 0.375), MAT["station"], phys="static")
        add_semantic(table_base.GetPrim(), "pack_station")
        box(P2 + "/Table_top",  (1.0, 0.7, 0.06), (table_x, pack_cy, 0.78),  MAT["cabinet"], phys="static")
        stack_light(P2 + "/Table_sl", table_x + 0.55, pack_cy + 0.35, 0.83)
        # outbound-carton stock, staged toward the zone's east (outbound) side
        crate_stack(P2 + "/Outbound0", pack_x + 0.9, pack_cy + 1.0, 2)
        crate_stack(P2 + "/Outbound1", pack_x + 0.9, pack_cy - 1.0, 3)
        crate_stack(P2 + "/Outbound2", pack_x + 1.4, pack_cy + 0.0, 2)

        # ---- loading-dock door on the SOUTH wall, right below the pack zone, so outbound
        # cartons have somewhere to leave the building. It is a wall-panel decal + jambs +
        # hazard threshold: no opening is cut, the wall still blocks physically. (It sits on
        # the south wall rather than the east one because the east side of Hall A is now the
        # shared wall between the two halls.) ----
        dock_w, dock_h = 2.4, 2.6
        dock_x = pack_x
        dock = box(ROOT + "/Walls/DockDoor", (dock_w, 0.08, dock_h), (dock_x, gy0 + 0.04, dock_h / 2.0), MAT["cabinet"])
        add_semantic(dock.GetPrim(), "dock_door")
        for e, ex in enumerate((dock_x - dock_w / 2.0 + 0.15, dock_x + dock_w / 2.0 - 0.15)):
            box(ROOT + "/Walls/DockJamb_%d" % e, (0.15, 0.15, dock_h), (ex, gy0 + 0.1, dock_h / 2.0), MAT["rail"], phys="static")
        box(ROOT + "/Markings/DockThreshold", (dock_w, 0.9, 0.02), (dock_x, gy0 + 0.5, 0.021), MAT["lane"])

# ---- lighting: bright cool overhead grid + a soft ambient dome ----
# the dome is authored once outside the hall subtree, so copying the hall does not double it
dome = UsdLux.DomeLight.Define(stage, WORLD + "/Dome")
dome.GetIntensityAttr().Set(300)
for k in range(5):
    lx = gx0 + (k + 0.5) * (gx1 - gx0) / 5.0
    for r, ry in enumerate((aisle_cy, CELL_Y)):
        rl = UsdLux.RectLight.Define(stage, ROOT + "/Lights/Ceil_%d_%d" % (k, r))
        rl.GetWidthAttr().Set(2.2)
        rl.GetHeightAttr().Set(0.8)
        rl.GetIntensityAttr().Set(9000 * RNG.uniform(0.9, 1.1))   # v5: small jitter so the grid
                                                                    # doesn't read as perfectly uniform
        UsdGeom.Xformable(rl).AddTranslateOp().Set(Gf.Vec3d(lx, ry, WALL_H - 0.15))

# A tighter, warmer light low over the pack table - a flat overhead grid alone reads
# like a photo studio.
if USE_PICKZONE and USE_PACKSTATION:
    tl = UsdLux.RectLight.Define(stage, ROOT + "/Lights/Task_pack")
    tl.GetWidthAttr().Set(0.6)
    tl.GetHeightAttr().Set(0.6)
    tl.GetIntensityAttr().Set(4000)
    tl.GetColorAttr().Set(Gf.Vec3f(1.0, 0.93, 0.82))
    UsdGeom.Xformable(tl).AddTranslateOp().Set(Gf.Vec3d(table_x, pack_cy, WALL_H - 1.0))

# ======================= twin hall: copy the hall, then close the seam ==================
# Hall B is a straight copy of Hall A shifted east by exactly one hall width, so B's west
# wall plane lands on A's east wall plane. Neither hall authors a wall there (see the
# perimeter-wall block above); the shared wall is built once here, with the passage left out
# of it. Everything is copied flat into the same layer, so the result is one self-contained
# USD with no external dependency other than the optional Isaac props.
HALL_B = WORLD + "/Hall_B"
DX = gx1 - gx0                                    # hall pitch = hall width

_layer = stage.GetEditTarget().GetLayer()
if stage.GetPrimAtPath(HALL_B):
    stage.RemovePrim(HALL_B)
Sdf.CopySpec(_layer, Sdf.Path(ROOT), _layer, Sdf.Path(HALL_B))
UsdGeom.Xformable(stage.GetPrimAtPath(HALL_B)).AddTranslateOp().Set(Gf.Vec3d(DX, 0.0, 0.0))

# ---- Hall B: same hall, different layout ----
# A pixel-identical copy next door reads as a copy, so Hall B gets its fixtures moved
# around: the storage zone and the corner rack swap ends of the hall, the stalled unit
# stands somewhere else in the aisle, and the loose clutter is shuffled. Nothing is moved
# into the band the passage opens onto.
def shift_prims(parent, name_prefix, dx=0.0, dy=0.0):
    # Offsets every prim under `parent` whose name starts with `name_prefix`. Only the
    # topmost translate op on each branch is touched - children below it are positioned
    # relative to it (a compound rigid body, or a referenced prop under its wrapper).
    parent_prim = stage.GetPrimAtPath(parent)
    if not parent_prim:
        return 0
    moved = 0
    stack = [c for c in parent_prim.GetChildren() if c.GetName().startswith(name_prefix)]
    while stack:
        prim = stack.pop()
        ops = [op for op in UsdGeom.Xformable(prim).GetOrderedXformOps()
               if op.GetOpType() == UsdGeom.XformOp.TypeTranslate]
        if ops:
            v = ops[0].Get()
            ops[0].Set(Gf.Vec3d(v[0] + dx, v[1] + dy, v[2]))
            moved += 1
            continue
        stack.extend(prim.GetChildren())
    return moved


# Hall B has no picking bay: the cluttered goods area is what made the two halls read as
# the same room twice. Removing it leaves that corner as open floor, and the handover strip
# that pointed into it goes with it. The pack zone stays.
for _gone in (HALL_B + "/PickZone", HALL_B + "/Handover"):
    if stage.GetPrimAtPath(_gone):
        stage.RemovePrim(_gone)

STORAGE_DY = -11.3          # storage zone: north-east corner -> south-east corner
CORNER_DY  = 12.5           # corner rack: south wall -> north-east, where storage was
for parent, prefix, dx_, dy_ in [
    (HALL_B + "/Storage", "Floor",       0.0, STORAGE_DY),
    (HALL_B + "/Storage", "Rack_",       0.0, STORAGE_DY),
    (HALL_B + "/Storage", "RackLoad_",   0.0, STORAGE_DY),
    (HALL_B + "/Storage", "Stock",       0.0, STORAGE_DY),
    (HALL_B + "/Storage", "ApronN",      0.0, STORAGE_DY / 2.0),
    (HALL_B + "/Products", "Vac_0",      0.0, STORAGE_DY),
    (HALL_B + "/Products", "Vac_1",      0.0, STORAGE_DY),
    (HALL_B + "/Products", "Vac_2",      0.0, STORAGE_DY),
    (HALL_B + "/Storage", "RackCorner",  0.0, CORNER_DY),
    (HALL_B + "/Obstacle", "StalledUnit", -6.0, 0.0),
    (HALL_B + "/Markings", "Detour_",    -6.0, 0.0),
    (HALL_B + "/Storage", "Stage0",       4.2, 0.0),
    (HALL_B + "/Storage", "Stage1",      -3.6, 0.9),
    (HALL_B + "/Assets",  "Extra_0",      6.4, 0.0),    # trolleys
    (HALL_B + "/Assets",  "Extra_1",      4.8, -0.5),
    (HALL_B + "/Assets",  "Extra_4",      2.5, 0.0),    # aisle signs
    (HALL_B + "/Assets",  "Extra_6",      0.0, STORAGE_DY),   # storage crates
    (HALL_B + "/Assets",  "Extra_7",      0.0, STORAGE_DY),
    (HALL_B + "/Assets",  "Extra_8",      0.0, STORAGE_DY),
    (HALL_B + "/Assets",  "Clutter_2",   -2.0, 0.6),
    (HALL_B + "/Assets",  "Clutter_5",    1.6, -0.7),
]:
    shift_prims(parent, prefix, dx_, dy_)

# ---- shared wall + passage ----
# drop the two walls that now sit back to back on the seam, and build one wall in their place
for _dead in (ROOT + "/Walls/East", HALL_B + "/Walls/West"):
    for _suffix in ("", "_wainscot", "_skirt"):
        if stage.GetPrimAtPath(_dead + _suffix):
            stage.RemovePrim(_dead + _suffix)

JUNC = WORLD + "/Junction"
UsdGeom.Xform.Define(stage, JUNC)
jx = gx1                                          # the shared wall plane
gap_cy = aisle_cy if HALL_GAP_Y is None else HALL_GAP_Y
gap_lo, gap_hi = gap_cy - HALL_GAP / 2.0, gap_cy + HALL_GAP / 2.0
if not (gy0 < gap_lo and gap_hi < gy1):
    raise ValueError("passage (%.2f m at y=%.2f) does not fit in the shared wall y=[%.2f, %.2f]"
                     % (HALL_GAP, gap_cy, gy0, gy1))

for nm, y0_, y1_ in (("South", gy0, gap_lo), ("North", gap_hi, gy1)):
    seg_len = y1_ - y0_
    seg_cy = (y0_ + y1_) / 2.0
    w = box(JUNC + "/Wall" + nm, (WALL_T, seg_len, WALL_H), (jx, seg_cy, WALL_H / 2.0),
            MAT["wall"], phys="static")
    add_semantic(w.GetPrim(), "wall")
    # wainscot/skirt on both faces, same recipe as the perimeter walls
    box(JUNC + "/Wall" + nm + "_wainscot", (WALL_T + 0.02, seg_len, 0.5),
        (jx, seg_cy, 1.05), MAT["wainscot"])
    box(JUNC + "/Wall" + nm + "_skirt", (WALL_T + 0.04, seg_len, 0.18),
        (jx, seg_cy, 0.09), MAT["skirt"])

# door jambs framing the opening + a painted threshold across it, so the passage reads as a
# doorway rather than a hole. Nothing is authored inside the opening itself: no collider, no
# step, so anything that fits through it can drive straight between the halls.
for e, jy in enumerate((gap_lo - 0.15, gap_hi + 0.15)):
    box(JUNC + "/Jamb_%d" % e, (0.30, 0.30, WALL_H), (jx, jy, WALL_H / 2.0),
        MAT["rail"], phys="static")
box(JUNC + "/Threshold", (1.6, HALL_GAP, 0.015), (jx, gap_cy, 0.013), MAT["aisle"])
for e, ex in enumerate((jx - 0.8, jx + 0.8)):
    box(JUNC + "/ThresholdEdge_%d" % e, (0.10, HALL_GAP, 0.025), (ex, gap_cy, 0.02), MAT["lane"])

print("[twin hall map] two halls under", WORLD,
      "| hall = %.2f x %.2f x %.1f m | overall = %.2f x %.2f m"
      % (gx1 - gx0, gy1 - gy0, WALL_H, 2.0 * (gx1 - gx0), gy1 - gy0),
      "| passage %.1f m wide at y=%.2f in the shared wall x=%.2f" % (HALL_GAP, gap_cy, jx),
      "| assets=%s pickzone=%s packzone=%s obstacle=%s products=%s semantics=%s"
      % (ASSETS_ON, USE_PICKZONE, USE_PACKSTATION and USE_PICKZONE, USE_OBSTACLE,
         USE_PRODUCTS, USE_SEMANTICS))

if ARGS is not None:                                # standalone run -> write the file out
    stage.GetRootLayer().Save()
    print("[twin hall map] saved:", os.path.abspath(ARGS.output))
