# Memory Guidelines for KPI Optimization Pipelines

## Server Constraints

- **Total RAM:** 7.6 GB (as of Feb 2026)
- **Safe threshold:** 70% (~5.3 GB) — enforced by `_check_memory()` in pipeline scripts
- **No swap** configured — OOM kills are fatal with no recovery

## Incident: Phase 18 OOM Kill (2026-03-02)

### What happened

`phase18_master.py` loaded all 5 timeframes (4H, 1D, 1W, 2W, 1M) into memory
simultaneously — both raw DataFrames (`data_by_tf`) and precomputed NumPy arrays
(`all_pc_by_tf`). Peak memory exceeded 5 GB, triggering an OS OOM kill during
Phase 18.1 (combo search).

### Memory cost per timeframe

| Component | Per TF | Formula |
|---|---|---|
| Raw DataFrames | ~800 MB | 268 stocks × 190 cols × ~2000 rows × 8 bytes |
| Precomputed arrays | ~200 MB | 268 stocks × (38 bool arrays + price/ATR/SMA) |
| **Total per TF** | **~1 GB** | |
| **5 TFs loaded at once** | **~5 GB** | OOM on 7.6 GB server |

### Root cause

```python
# BAD: holds all TFs in memory simultaneously
data_by_tf = {}
for tf in ALL_TFS:
    data_by_tf[tf] = load_data(tf)  # ~800 MB each, never freed
```

### Fix applied

Restructured `main()` to process one TF at a time:

```python
# GOOD: load → process → free → next TF
for tf in available_tfs:
    data = load_data(tf)             # ~800 MB
    all_pc = precompute(data, tf)    # ~200 MB
    del data; gc.collect()           # free 800 MB immediately

    # run 18.1 → 18.5 on this TF's data only
    ...

    del all_pc; gc.collect()         # free 200 MB before next TF
```

Peak memory: ~1 GB (single TF) instead of ~5 GB (all TFs).

## Rules for Future Phases

### 1. Never hold multiple timeframes in memory simultaneously

Process one TF at a time. Free it (`del` + `gc.collect()`) before loading the next.

### 2. Delete raw DataFrames after precompute

`precompute()` extracts all needed arrays from the raw DataFrames. The originals
(~800 MB per TF) are no longer needed after that call.

### 3. Use the memory guard

```python
import gc, psutil

def _check_memory(label="", threshold=70):
    mem = psutil.virtual_memory()
    if mem.percent > threshold:
        gc.collect()
        mem = psutil.virtual_memory()
        if mem.percent > threshold:
            raise MemoryError(f"Memory at {mem.percent}% (>{threshold}%)")
```

Call it before every `load_data()`.

### 4. Consider column filtering in load_data

The full parquet has ~190 columns. Only ~45 are needed (OHLCV + KPI columns).
Adding a column filter to `pd.read_parquet()` cuts raw DF memory by ~75%.

### 5. Use nohup for long-running pipelines

```bash
cd /root/damiverse_apps/trading_app
nohup .venv/bin/python3 research/kpi_optimization/phase18_master.py \
  > research/kpi_optimization/phase18.log 2>&1 &
```

This prevents Cursor terminal closure from killing the process.

### 6. Monitor during execution

```bash
# Memory usage
free -h

# Process status
ps aux | grep phase18

# Output progress
tail -50 research/kpi_optimization/phase18.log
```
