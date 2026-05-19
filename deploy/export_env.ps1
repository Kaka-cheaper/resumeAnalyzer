# 把 .env 加载到当前 PowerShell 进程，供 s deploy 使用
# 用法（必须 dot-source 到当前进程）：
#   . .\deploy\export_env.ps1
# 或者在 deploy 目录里：
#   . .\export_env.ps1

$envFile = Resolve-Path (Join-Path $PSScriptRoot "..\.env") -ErrorAction SilentlyContinue
if (-not $envFile) {
    Write-Error ".env 文件不存在：$(Join-Path $PSScriptRoot '..\.env')"
    return
}

$count = 0
Get-Content $envFile.Path -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    if ($line -notmatch "^([^=]+)=(.*)$") { return }
    $key = $Matches[1].Trim()
    $val = $Matches[2].Trim().Trim("'`"")
    [Environment]::SetEnvironmentVariable($key, $val, "Process")
    $count++
}

Write-Host "Loaded $count env vars from $($envFile.Path)"
if ($env:MIMO_API_KEY) {
    $k = $env:MIMO_API_KEY
    $masked = "$($k.Substring(0,[Math]::Min(4,$k.Length)))...$($k.Substring([Math]::Max(0,$k.Length-4)))"
    Write-Host "  MIMO_API_KEY  : $masked"
}
Write-Host "  MIMO_BASE_URL : $env:MIMO_BASE_URL"
Write-Host "  MIMO_MODEL    : $env:MIMO_MODEL"
