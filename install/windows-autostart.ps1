# 安装 Windows 开机自启（启动文件夹快捷方式）
$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script = Join-Path $dir "windows\sysmon_card.pyw"
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "sysmon-card.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnk)
$shortcut.TargetPath = "pythonw.exe"
$shortcut.Arguments = "`"$script`""
$shortcut.WorkingDirectory = Split-Path -Parent $script
$shortcut.Save()

Write-Host "已创建开机自启快捷方式：$lnk"
