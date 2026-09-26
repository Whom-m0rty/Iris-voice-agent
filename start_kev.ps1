# Starts the local Kev-4B decision server on http://127.0.0.1:8009
# Measured on RTX 5060 Ti 16 GB (2026-09-26): this config = ~217 ms per decision, 9.7 GB VRAM.
# CUDA graphs are OFF on purpose: they capture one graph per input shape, fill all 16 GB,
# Windows spills VRAM to system RAM and a decision takes 10+ seconds.
# Run the venv python directly, not `uv run` - uv would reinstall the CPU-only torch.
$env:HF_HUB_DISABLE_SYMLINKS = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:KEV_CUDA_GRAPHS = '0'
$env:KEV_FUSED = '0'
Set-Location "$PSScriptRoot\kev"
.\.venv\Scripts\python.exe -m kev.serve --run jaredpalmer/kev-4b --port 8009
