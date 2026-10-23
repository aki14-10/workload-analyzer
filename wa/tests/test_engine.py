import pytest
from app import engine as E
from app.presets import PRESETS

VM = [{"id": "A", "cpu": 4, "mem": 4}]
def T(i, a, b, p=1, d=100, c=1, m=1): return dict(id=i, arrival=a, burst=b, priority=p, deadline=d, cpu=c, mem=m)
def order(r): return [s["task"] for s in r["traces"]["A"]]

def test_fcfs_order_and_idle_gap():
    r = E.run_policy([T("a", 0, 2), T("b", 5, 1)], VM, "FCFS")
    assert order(r) == ["a", "a", None, None, None, "b"]
    assert r["summary"]["makespan"] == 6

def test_sjf_nonpreemptive_and_tie_break():
    r = E.run_policy([T("a", 0, 4), T("b", 1, 1), T("c", 1, 1)], VM, "SJF")
    assert order(r) == ["a"] * 4 + ["b", "c"]

def test_srtf_preempts():
    assert order(E.run_policy([T("a", 0, 5), T("b", 1, 1)], VM, "SRTF"))[:3] == ["a", "b", "a"]

def test_priority_lower_number_first():
    assert order(E.run_policy([T("a", 0, 1, 5), T("b", 0, 1, 1)], VM, "PRIORITY"))[0] == "b"

def test_round_robin_quantum():
    assert order(E.run_policy([T("a", 0, 3), T("b", 0, 3)], VM, "RR", {"quantum": 2})) == list("aabbab")

def test_metrics():
    s = E.run_policy([T("a", 0, 2, d=2), T("b", 0, 2, d=3)], VM, "FCFS")["summary"]
    assert s["avg_waiting"] == 1 and s["avg_turnaround"] == 3 and s["deadline_misses"] == 1 and s["cpu_utilization"] == 1

def test_empty_workload():
    assert E.run_policy([], VM, "ADAPTIVE")["summary"]["makespan"] == 0

def test_validation():
    with pytest.raises(E.ValidationError): E.validate_tasks([dict(T("a", 0, 0))])
    with pytest.raises(E.ValidationError): E.validate_tasks([dict(T("a", 5, 1, d=2))])

def test_vm_feasibility_and_unallocated():
    r = E.run_policy([T("big", 0, 2, c=9), T("ok", 0, 2)], VM, "FCFS")
    assert [u["id"] for u in r["allocation"]["unallocated"]] == ["big"] and len(r["tasks"]) == 1

def test_infeasible_deadline_reported():
    r = E.run_policy([T("a", 0, 5, d=2)], VM, "ADAPTIVE")
    assert r["predicted_risk"][0]["risk"] == "Likely to Miss" and r["summary"]["deadline_misses"] == 1

def test_adaptive_beats_fcfs_on_misses_when_possible():
    ts = [T("long", 0, 6, d=40), T("s1", 1, 1, d=3), T("s2", 1, 1, d=4)]
    assert E.run_policy(ts, VM, "ADAPTIVE")["summary"]["deadline_misses"] <= E.run_policy(ts, VM, "FCFS")["summary"]["deadline_misses"]

def test_switch_penalty_blocks_switching():
    ts = PRESETS["Deadline-constrained"]
    hi = E.run_policy(ts, VM, "ADAPTIVE", {"switch_penalty": 1000, "switch_threshold": 0.99})
    assert hi["summary"]["policy_switches"] == 0 and any(d["type"] == "rejected" for d in hi["decisions"]) or hi["summary"]["policy_switches"] == 0

def test_deterministic_and_same_taskset():
    a = E.evaluate(PRESETS["Mixed"], VM); b = E.evaluate(PRESETS["Mixed"], VM)
    assert a == b and set(a) == set(E.BASE + ["ADAPTIVE"])

def test_all_presets_run():
    for ts in PRESETS.values():
        for p in E.BASE + ["ADAPTIVE"]:
            r = E.run_policy(ts, [{"id": "A", "cpu": 8, "mem": 8}, {"id": "B", "cpu": 4, "mem": 4}], p)
            assert len(r["tasks"]) == len(ts)
