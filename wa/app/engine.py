"""Scheduling engine: baselines, adaptive deadline-risk policy, allocation, metrics.
Time unit: integer ticks. Each VM executes one task per tick (single execution slot)."""
import copy

BASE = ["FCFS", "SJF", "SRTF", "PRIORITY", "RR"]
CANDIDATES = {"deadline-critical": ["EDF", "SRTF", "SJF"], "short-job-heavy": ["SJF", "SRTF"],
              "interactive": ["RR", "SRTF"], "mixed": ["SJF", "RR", "EDF"]}
DEFAULT_CFG = {"quantum": 2, "switch_penalty": 1, "switch_threshold": 0.05}


class ValidationError(ValueError):
    pass


def validate_tasks(raw):
    out, seen = [], set()
    for r in raw:
        tid = str(r.get("id", "")).strip()
        if not tid:
            raise ValidationError("Task ID is required.")
        if tid in seen:
            raise ValidationError(f"Duplicate task ID: {tid}.")
        seen.add(tid)
        try:
            t = {k: int(r[k]) for k in ("arrival", "burst", "priority", "deadline", "cpu", "mem")}
        except (KeyError, TypeError, ValueError):
            raise ValidationError(f"Task {tid}: all numeric fields must be integers.")
        if t["burst"] <= 0:
            raise ValidationError(f"Task {tid}: burst time must be greater than zero.")
        if t["arrival"] < 0:
            raise ValidationError(f"Task {tid}: arrival time cannot be negative.")
        if t["deadline"] < t["arrival"]:
            raise ValidationError(f"Task {tid}: deadline precedes arrival time.")
        if t["cpu"] <= 0 or t["mem"] <= 0:
            raise ValidationError(f"Task {tid}: CPU and memory requirements must be positive.")
        t["id"] = tid
        out.append(t)
    return out


def validate_vms(raw):
    out, seen = [], set()
    for r in raw:
        vid = str(r.get("id", "")).strip()
        if not vid or vid in seen:
            raise ValidationError("VM identifiers must be non-empty and unique.")
        seen.add(vid)
        try:
            cpu, mem = int(r["cpu"]), int(r["mem"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError(f"VM {vid}: capacities must be integers.")
        if cpu <= 0 or mem <= 0:
            raise ValidationError(f"VM {vid}: capacities must be positive.")
        out.append({"id": vid, "cpu": cpu, "mem": mem})
    return out


def allocate(tasks, vms):
    """Feasibility filter (cpu, mem), then least normalized load (assigned burst / CPU capacity)."""
    load = {v["id"]: 0 for v in vms}
    assign, why, unalloc = {}, {}, []
    for t in sorted(tasks, key=lambda t: (t["arrival"], t["id"])):
        feas = [v for v in vms if v["cpu"] >= t["cpu"] and v["mem"] >= t["mem"]]
        if not feas:
            unalloc.append({"id": t["id"], "reason": f"Insufficient VM capacity: requires cpu {t['cpu']}, mem {t['mem']}."})
            continue
        v = min(feas, key=lambda v: (load[v["id"]] / v["cpu"], v["id"]))
        assign[t["id"]] = v["id"]
        why[t["id"]] = f"{v['id']} selected: lowest normalized load among {len(feas)} feasible VM(s)."
        load[v["id"]] += t["burst"]
    return {"assign": assign, "why": why, "unallocated": unalloc}


# ---------- core stepping ----------
def new_state(tasks):
    return {"t": 0, "rem": {x["id"]: x["burst"] for x in tasks}, "T": {x["id"]: x for x in tasks},
            "last": None, "rrq": [], "seen": set(), "qleft": 0}


def _step(st, pol, q):
    t, T, rem = st["t"], st["T"], st["rem"]
    if pol == "RR":
        for i in sorted((i for i in T if T[i]["arrival"] <= t and i not in st["seen"]), key=lambda i: (T[i]["arrival"], i)):
            st["seen"].add(i)
            st["rrq"].append(i)
        l = st["last"]
        if l and rem[l] > 0 and st["qleft"] <= 0:
            st["rrq"].append(l)
    ready = [i for i in T if T[i]["arrival"] <= t and rem[i] > 0]
    run, l = None, st["last"]
    if ready:
        if pol == "RR":
            if l and rem[l] > 0 and st["qleft"] > 0:
                run = l
            else:
                run = st["rrq"].pop(0)
                st["qleft"] = q
        elif pol in ("FCFS", "SJF", "PRIORITY") and l in ready:
            run = l  # non-preemptive
        else:
            key = {"FCFS": lambda i: (T[i]["arrival"], i), "SJF": lambda i: (T[i]["burst"], T[i]["arrival"], i),
                   "PRIORITY": lambda i: (T[i]["priority"], T[i]["arrival"], i),
                   "SRTF": lambda i: (rem[i], T[i]["arrival"], i), "EDF": lambda i: (T[i]["deadline"], i)}[pol]
            run = min(ready, key=key)
    if run:
        rem[run] -= 1
        st["qleft"] -= 1
    st["last"] = run
    st["t"] = t + 1
    return run


def finish(st, pol, q):
    """Completion times if `pol` is used from the state onward (declared workload is known)."""
    st = copy.deepcopy(st)
    comp = {}
    while any(v > 0 for v in st["rem"].values()):
        r = _step(st, pol, q)
        if r and st["rem"][r] == 0:
            comp[r] = st["t"]
    return comp


def risk_class(slack, burst):
    return "Likely to Miss" if slack < 0 else ("At Risk" if slack < burst else "Low Risk")


def classify(tasks, comp):
    n = len(tasks)
    if n == 0:
        return "mixed"
    risky = sum(1 for x in tasks if risk_class(x["deadline"] - comp[x["id"]], x["burst"]) != "Low Risk")
    short = sum(1 for x in tasks if x["burst"] <= 3)
    late = sum(1 for x in tasks if x["arrival"] > 0)
    if risky / n >= 0.3:
        return "deadline-critical"
    if short / n >= 0.6:
        return "short-job-heavy"
    if short / n >= 0.4 and late / n >= 0.5:
        return "interactive"
    return "mixed"


def _estimate(st, pol, q, penalty):
    comp = finish(st, pol, q)
    T = st["T"]
    adj = {i: c + penalty for i, c in comp.items()}
    return {"comp": comp, "misses": sum(1 for i, c in adj.items() if c > T[i]["deadline"]),
            "sum_c": sum(comp.values())}


def reassess(st, cur, q, cfg, vm, log):
    T, rem = st["T"], st["rem"]
    unf = [T[i] for i in T if rem[i] > 0]
    if not unf:
        return cur
    ref = finish(st, cur or "FCFS", q)
    cls = classify(unf, ref)
    pen = 0 if cur is None else cfg["switch_penalty"]
    cands = list(dict.fromkeys(CANDIDATES[cls]))
    ests = {p: _estimate(st, p, q, pen) for p in cands}
    best = min(cands, key=lambda p: (ests[p]["misses"], ests[p]["sum_c"], p))
    base = dict(time=st["t"], vm=vm, workload_class=cls)
    if cur is None:
        log.append({**base, "type": "initial", "from": None, "to": best,
                    "reason": f"Workload classified as {cls}; {best} has the lowest predicted misses ({ests[best]['misses']}) then total completion ({ests[best]['sum_c']})."})
        return best
    cur_e = _estimate(st, cur, q, 0)
    if best == cur:
        return cur
    c = ests[best]
    gain = cur_e["sum_c"] - c["sum_c"]
    if c["misses"] < cur_e["misses"] or (c["misses"] == cur_e["misses"] and gain > cfg["switch_threshold"] * cur_e["sum_c"]):
        log.append({**base, "type": "switch", "from": cur, "to": best,
                    "reason": f"Workload class {cls}. Predicted misses {cur_e['misses']} -> {c['misses']} (penalty {pen} included); total completion {cur_e['sum_c']} -> {c['sum_c']}."})
        # hand over to the new policy
        st["seen"] = {i for i in T if T[i]["arrival"] <= st["t"]}
        st["rrq"] = sorted((i for i in st["seen"] if rem[i] > 0 and i != st["last"]), key=lambda i: (T[i]["arrival"], i))
        st["qleft"] = 0
        return best
    log.append({**base, "type": "rejected", "from": cur, "to": best,
                "reason": f"Candidate {best} rejected: predicted misses {cur_e['misses']} vs {c['misses']}, completion gain {gain} below threshold {cfg['switch_threshold']:.0%} of {cur_e['sum_c']} after penalty {pen}."})
    return cur


def simulate_vm(tasks, policy, q, cfg, vm="VM"):
    st = new_state(tasks)
    trace, log, cur, ctx, switches, prev = [], [], None, 0, 0, None
    arrivals = {x["arrival"] for x in tasks}
    pol = None if policy == "ADAPTIVE" else policy
    check = True
    while any(v > 0 for v in st["rem"].values()):
        if policy == "ADAPTIVE" and (check or st["t"] in arrivals):
            new = reassess(st, pol, q, cfg, vm, log)
            if pol is not None and new != pol:
                switches += 1
            pol = new
            check = False
        run = _step(st, pol or policy, q)
        if run and st["rem"][run] == 0:
            check = True
        if run and prev and run != prev:
            ctx += 1
        if run:
            prev = run
        trace.append({"task": run, "policy": pol})
    return {"trace": trace, "log": log, "ctx": ctx, "switches": switches}


def run_policy(tasks, vms, policy, cfg=None):
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    q = cfg["quantum"]
    if q <= 0:
        raise ValidationError("Time quantum must be positive.")
    al = allocate(tasks, vms)
    placed = [t for t in tasks if t["id"] in al["assign"]]
    traces, log, ctx, sw = {}, [], 0, 0
    tmap = {}
    for v in vms:
        mine = [t for t in placed if al["assign"][t["id"]] == v["id"]]
        r = simulate_vm(mine, policy, q, cfg, v["id"]) if mine else {"trace": [], "log": [], "ctx": 0, "switches": 0}
        traces[v["id"]] = r["trace"]
        log += r["log"]; ctx += r["ctx"]; sw += r["switches"]
        for t in mine:
            tmap[t["id"]] = (v["id"], t)
    rows = []
    for tid, (vid, t) in tmap.items():
        ticks = [i for i, s in enumerate(traces[vid]) if s["task"] == tid]
        comp = ticks[-1] + 1
        rows.append({"id": tid, "vm": vid, "arrival": t["arrival"], "burst": t["burst"], "deadline": t["deadline"],
                     "start": ticks[0], "completion": comp, "waiting": comp - t["arrival"] - t["burst"],
                     "turnaround": comp - t["arrival"], "response": ticks[0] - t["arrival"],
                     "slack": t["deadline"] - comp, "missed": comp > t["deadline"]})
    rows.sort(key=lambda r: r["id"])
    n = len(rows)
    mk = max((r["completion"] for r in rows), default=0)
    busy = {vid: sum(1 for s in tr if s["task"]) for vid, tr in traces.items()}
    avg = lambda k: round(sum(r[k] for r in rows) / n, 3) if n else 0
    miss = sum(r["missed"] for r in rows)
    summary = {"policy": policy, "avg_waiting": avg("waiting"), "avg_turnaround": avg("turnaround"),
               "avg_response": avg("response"), "makespan": mk, "deadline_misses": miss,
               "miss_rate": round(miss / n, 3) if n else 0,
               "cpu_utilization": round(sum(busy.values()) / (mk * len(vms)), 3) if mk and vms else 0,
               "vm_utilization": {k: round(b / mk, 3) if mk else 0 for k, b in busy.items()},
               "context_switches": ctx, "policy_switches": sw, "switching_overhead": sw * cfg["switch_penalty"]}
    risk = []  # predicted at t=0, kept separate from actual outcomes
    for v in vms:
        mine = [t for t in placed if al["assign"][t["id"]] == v["id"]]
        if not mine:
            continue
        p0 = policy if policy != "ADAPTIVE" else next((e["to"] for e in log if e["vm"] == v["id"] and e["type"] == "initial"), "FCFS")
        comp = finish(new_state(mine), p0, q)
        for t in mine:
            s = t["deadline"] - comp[t["id"]]
            risk.append({"id": t["id"], "vm": v["id"], "estimated_completion": comp[t["id"]], "slack": s, "risk": risk_class(s, t["burst"])})
    return {"summary": summary, "tasks": rows, "traces": traces, "decisions": sorted(log, key=lambda e: (e["time"], e["vm"])),
            "allocation": al, "predicted_risk": sorted(risk, key=lambda r: r["id"]), "config": {**cfg, "policy": policy}}


def evaluate(tasks, vms, cfg=None):
    return {p: run_policy(tasks, vms, p, cfg)["summary"] for p in BASE + ["ADAPTIVE"]}
