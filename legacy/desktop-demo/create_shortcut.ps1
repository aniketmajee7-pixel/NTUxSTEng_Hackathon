$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$scriptPath = Join-Path $projectDirectory 'main.py'
if (-not (Test-Path $scriptPath)) { throw 'main.py is missing' }
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Windows Desktop Demo.lnk'
if (Test-Path $shortcutPath) { throw 'Shortcut already exists; move it elsewhere before recreating it.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $scriptPath + '"'
$shortcut.WorkingDirectory = $projectDirectory
$shortcut.Description = 'Browser and inventory GUI demonstration'
$shortcut.Save()
Write-Host "Created: $shortcutPath"
