# 安全清理 C 盘缓存腾空间，不删用户数据
$beforeFree = (Get-PSDrive C).Free
Write-Host "Before: $([math]::Round($beforeFree/1GB,2)) GB free"

# 看哪些缓存目录占空间
$paths = @(
    "$env:LOCALAPPDATA\pip\Cache",
    "$env:APPDATA\npm-cache",
    "$env:LOCALAPPDATA\npm-cache",
    "$env:USERPROFILE\.cache",
    "$env:TEMP"
)
foreach ($p in $paths) {
    if (Test-Path $p) {
        try {
            $size = (Get-ChildItem $p -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
            if ($size -gt 0) {
                Write-Host ("  {0,-60} {1,8:N1} MB" -f $p, ($size/1MB))
            }
        } catch {}
    }
}

# 清 pip 临时目录残留
Get-ChildItem $env:TEMP -Filter "pip-*" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $env:TEMP -Filter "tmp*" -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Cleaned pip temp residuals"

# 清 Temp 里 7 天前的内容
Get-ChildItem $env:TEMP -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-7) } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Cleaned Temp older than 7 days"

# pip cache purge
python -m pip cache purge 2>&1 | Out-Null
Write-Host "Purged pip cache"

$afterFree = (Get-PSDrive C).Free
$gained = ($afterFree - $beforeFree) / 1GB
Write-Host ("After: {0:N2} GB free  (gained {1:N2} GB)" -f ($afterFree/1GB), $gained)
