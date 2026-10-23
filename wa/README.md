# Workload Analyzer: Adaptive Cloud Task Scheduler with Deadline-Risk Prediction

## Setup and run (Windows PowerShell)
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    python -m pytest -q
    uvicorn app.main:app --reload
Open http://127.0.0.1:8000

## Model
- Time unit: integer ticks. Each VM has one execution slot and schedules its own task queue. Tasks are placed by feasibility (CPU, memory) then lowest normalized load.
- Priority: lower number means higher priority. Ties: arrival time, then task ID. RR uses the configured quantum.
- Adaptive policy: workload class (deadline-critical, short-job-heavy, interactive, mixed) selects candidate policies (EDF, SRTF, SJF, RR). Candidates are scored by forward simulation using the same model: predicted deadline misses first, then total completion time (lexicographic, no weights). A switch is accepted only if predicted misses fall, or completion improves by more than the threshold, after a switch penalty delays all candidate completions. Checkpoints: time zero, task arrivals, task completions.
- Assumptions and limits: the declared workload (including future arrivals) is known to the estimator; the switch penalty is an estimation cost and is not inserted into the executed timeline; EDF is used only inside the adaptive policy; Priority aging is not implemented.
- Predicted risk (t=0) is reported separately from actual deadline outcomes.

## Layout
app/engine.py (scheduling, allocation, metrics), app/main.py (REST API), app/presets.py, app/static/index.html, tests/.
