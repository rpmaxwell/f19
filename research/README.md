# research/

Reverse-engineering and investigation tools: memory surveys, correlation trials, debugger and
keystroke bring-up, screenshot/OCR loggers, superseded planners, Ghidra drivers, notebooks.

This tree is **not** part of the `f19` package or its tests, and **it may break**. Many scripts
here still import product modules by their old flat names (`dosbox_automation`, `f19_state`, ...)
from their own directory, so they need a `sys.path` fix before they run again. Some of those flat modules still exist as
shims in `scripts/` and `analysis/` (see `docs/design/refactor_plan.md`, "Cutover steps 4 and 5"); `release_arrows` and `fix_after_load`
were deleted on 2026-10-30 (see git history; `Emulator.release_arrows` is the replacement).

- `scripts/`  live probes and trials (moved from `scripts/`, 2026-10-24)
- `analysis/` offline analysis and RE (moved from `analysis/`). `egame_re/` and `end_re/` hold
  game binaries, which are gitignored. `.venv-ghidra` is the old pyghidra venv; it was moved
  as-is and needs recreating (`uv sync --group research`).
- `notebooks/` the debugbox / breakpoint experiments

Data these tools wrote now lives under `F19_DATA` (default `~/f19-data`). The F-15 SE2
reconstruction they reference is at `~/projects/f15se2-re`.
