import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from . import engine as E
from .presets import PRESETS, DEFAULT_VMS

app = FastAPI(title="Workload Analyzer")
S = {"tasks": E.validate_tasks(PRESETS["Mixed"]), "vms": list(DEFAULT_VMS), "sim": None, "experiments": []}

def guard(fn):
    try:
        return fn()
    except E.ValidationError as e:
        raise HTTPException(422, str(e))

@app.get("/api/state")
def state(): return {"tasks": S["tasks"], "vms": S["vms"], "presets": list(PRESETS), "policies": E.BASE + ["ADAPTIVE"]}

@app.put("/api/workload")
def put_workload(body: list[dict]):
    S["tasks"] = guard(lambda: E.validate_tasks(body)); S["sim"] = None
    return {"count": len(S["tasks"])}

@app.put("/api/vms")
def put_vms(body: list[dict]):
    S["vms"] = guard(lambda: E.validate_vms(body)); S["sim"] = None
    return {"count": len(S["vms"])}

@app.post("/api/presets/{name}")
def load_preset(name: str):
    if name not in PRESETS: raise HTTPException(404, "Unknown preset.")
    S["tasks"] = E.validate_tasks(PRESETS[name]); S["sim"] = None
    return {"tasks": S["tasks"]}

@app.post("/api/allocate")
def allocate(): return E.allocate(S["tasks"], S["vms"])

@app.post("/api/run")
def run(body: dict):
    return guard(lambda: E.run_policy(S["tasks"], S["vms"], body.get("policy", "ADAPTIVE"), body.get("config")))

@app.post("/api/evaluate")
def evaluate(body: dict):
    res = guard(lambda: E.evaluate(S["tasks"], S["vms"], body.get("config")))
    rec = {"id": len(S["experiments"]) + 1, "tasks": S["tasks"], "vms": S["vms"], "config": {**E.DEFAULT_CFG, **(body.get("config") or {})}, "results": res}
    S["experiments"].append(rec)
    return rec

@app.get("/api/experiments")
def experiments(): return S["experiments"]

@app.post("/api/sim/start")
def sim_start(body: dict):
    r = guard(lambda: E.run_policy(S["tasks"], S["vms"], body.get("policy", "ADAPTIVE"), body.get("config")))
    S["sim"] = {"res": r, "t": 0}
    return sim_view()

@app.post("/api/sim/step")
def sim_step(body: dict = {}):
    if not S["sim"]: raise HTTPException(409, "No simulation is active. Start a simulation first.")
    S["sim"]["t"] = min(S["sim"]["t"] + 1, max((len(t) for t in S["sim"]["res"]["traces"].values()), default=0))
    return sim_view()

@app.post("/api/sim/reset")
def sim_reset():
    S["sim"] = None
    return {"active": False}

def sim_view():
    s = S["sim"]; r, t = s["res"], s["t"]
    gantt = {v: [x for x in tr[:t]] for v, tr in r["traces"].items()}
    running = {v: (tr[t - 1]["task"] if 0 < t <= len(tr) else None) for v, tr in r["traces"].items()}
    status = {}
    for row in r["tasks"]:
        status[row["id"]] = ("waiting" if row["arrival"] > t else "completed" if row["completion"] <= t
                             else "running" if row["id"] in running.values() else "ready")
    end = max((len(x) for x in r["traces"].values()), default=0)
    return {"active": True, "t": t, "finished": t >= end, "gantt": gantt, "running": running, "status": status,
            "decisions": [d for d in r["decisions"] if d["time"] < max(t, 1)], "summary": r["summary"] if t >= end else None,
            "policy": {v: (tr[t - 1]["policy"] if 0 < t <= len(tr) else None) for v, tr in r["traces"].items()}}

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
