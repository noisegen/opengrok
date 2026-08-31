# Start the DeepSeek hop on 127.0.0.1:18791.
# Key lives in %USERPROFILE%\.grokbot\deepseek.env — never in git or bindings.
$ErrorActionPreference = "Stop"
$envFile = Join-Path $env:USERPROFILE ".grokbot\deepseek.env"
$hop = Join-Path $PSScriptRoot "deepseek-hop.py"
if (-not (Test-Path $envFile)) {
    throw "Missing $envFile - put DEEPSEEK_API_KEY=... in that file first."
}
$hasKey = Get-Content $envFile | Where-Object { $_ -match '^\s*DEEPSEEK_API_KEY=\S+' }
if (-not $hasKey) {
    throw "DEEPSEEK_API_KEY is empty in $envFile"
}
$existing = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -eq "127.0.0.1" -and $_.LocalPort -eq 18791 }
if ($existing) {
    Write-Host "deepseek-hop already listening on :18791"
    exit 0
}
$log = Join-Path $env:USERPROFILE ".grokbot\deepseek-hop.log"
Start-Process -FilePath "python" -ArgumentList $hop -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $log
Start-Sleep -Seconds 2
$up = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -eq "127.0.0.1" -and $_.LocalPort -eq 18791 }
if (-not $up) { throw "hop did not bind :18791 — see $log" }
Write-Host "deepseek-hop LIVE http://127.0.0.1:18791"
