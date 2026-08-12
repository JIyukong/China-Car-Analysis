# CarApp 调度器 Windows 服务安装脚本（需管理员权限，需预先安装 NSSM 并加入 PATH）
# 用法: .\deploy\install_scheduler_nssm.ps1 -ProjectRoot C:\Path\To\CarApp -Python C:\Path\To\python.exe

param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$ServiceName = "CarAppScheduler",
    [string]$LogDir = ""
)

if (-not $LogDir) { $LogDir = Join-Path $ProjectRoot "logs" }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$outLog = Join-Path $LogDir "scheduler.out.log"
$errLog = Join-Path $LogDir "scheduler.err.log"

Write-Host "Installing service $ServiceName ..."
nssm install $ServiceName $Python "-m" "scheduler.runner"
nssm set $ServiceName AppDirectory $ProjectRoot
nssm set $ServiceName AppStdout $outLog
nssm set $ServiceName AppStderr $errLog
nssm set $ServiceName AppStdoutCreationDisposition 4   # append
nssm set $ServiceName AppStderrCreationDisposition 4
nssm set $ServiceName Start SERVICE_AUTO_START
nssm set $ServiceName AppExit Default Restart
nssm set $ServiceName AppRestartDelay 10000
nssm set $ServiceName AppEnvironmentExtra "APP_ENV=prod"

Write-Host "Starting service ..."
nssm start $ServiceName
Write-Host "Done. Check logs at $LogDir"
