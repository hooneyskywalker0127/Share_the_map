#!/usr/bin/env bash
# Open the twin-hall map in the Isaac Sim GUI.
MAP="$(cd "$(dirname "$0")" && pwd)/twin_hall_map.usd"
PY=${ISAACSIM_PY:-$HOME/miniconda3/envs/env_isaaclab/bin/python}
exec "$PY" - "$MAP" <<'PYEOF'
import sys
from isaacsim import SimulationApp
# create_new_stage defaults to True and would wipe the stage opened by open_usd
# (simulation_app.py: open_usd is applied first, then create_new_stage() replaces it),
# so it has to be turned off here or the app comes up on an empty stage.
app = SimulationApp({"headless": False,
                     "open_usd": sys.argv[1],
                     "create_new_stage": False})
import omni.usd
ctx = omni.usd.get_context()
for _ in range(600):                       # let the referenced props finish streaming in
    app.update()
    if ctx.get_stage_loading_status()[2] == 0:
        break
print("[open_map] prims on stage:", len(list(ctx.get_stage().Traverse())))
print("[open_map] opened:", sys.argv[1])
while app.is_running():
    app.update()
app.close()
PYEOF
