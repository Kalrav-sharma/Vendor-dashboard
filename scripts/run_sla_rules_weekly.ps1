# Wrapper for the Windows Scheduled Task "VendorPortal-LastMileSlaRules-Weekly".
# Runs sync_last_mile_sla_rules.py (VPN-only, see that script's own docstring)
# and appends timestamped output to a local, gitignored log -- never prints
# secret values, only what the script itself already prints (row counts).
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$log = Join-Path $PSScriptRoot ".sla_rules_sync.log"

Set-Location $repoRoot
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "`n=== $stamp ==="
try {
    python scripts/sync_last_mile_sla_rules.py *>> $log
    Add-Content -Path $log -Value "exit code: $LASTEXITCODE"
} catch {
    Add-Content -Path $log -Value "wrapper error: $_"
}
