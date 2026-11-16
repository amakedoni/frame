param(
    [ValidateSet('A', 'B')][string]$Annotator = 'A',
    [string]$Python = 'python',
    [switch]$Background
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pilotDirectory = Join-Path $projectRoot 'annotation/pilot/local'
if (-not (Test-Path -LiteralPath (Join-Path $pilotDirectory 'rdd_tasks.json'))) {
    throw 'Prepare tasks first: python scripts/prepare_annotation_pilot.py'
}
$environmentDirectory = Join-Path $projectRoot '.annotation-venv'
$annotationPython = Join-Path $environmentDirectory 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $annotationPython)) {
    & $Python -m venv $environmentDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the annotation environment.' }
}
$studioExecutable = Join-Path $environmentDirectory 'Scripts/label-studio.exe'
if (-not (Test-Path -LiteralPath $studioExecutable)) {
    & $annotationPython -m pip install -r (Join-Path $projectRoot 'annotation/requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot install Label Studio.' }
}
$env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED = 'true'
$env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT = $pilotDirectory.Replace('\', '/')
$env:LABEL_STUDIO_ANALYTICS_ENABLED = 'false'
$storageDirectory = Join-Path $projectRoot ".annotation-state/$Annotator"
$port = if ($Annotator -eq 'A') { 8081 } else { 8082 }
New-Item -ItemType Directory -Path $storageDirectory -Force | Out-Null
$env:LABEL_STUDIO_BASE_DATA_DIR = $storageDirectory
$existingListeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
if ($existingListeners.Count -gt 0) {
    foreach ($existingListener in $existingListeners) {
        $existingProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($existingListener.OwningProcess)"
        if ($existingProcess.CommandLine -notlike ('*' + $environmentDirectory + '*')) {
            throw "Port $port is occupied by another application."
        }
    }
    Write-Host "Annotation server already running: http://localhost:$port"
    return
}
Write-Host "Annotator $Annotator. Local storage: $pilotDirectory"
Write-Host "Open http://localhost:$port . Keep projects and exports separate."
if ($Background) {
    $serverArguments = @('start', '--data-dir', ('"' + $storageDirectory + '"'),
        '--port', $port, '--internal-host', '127.0.0.1', '--no-browser')
    $serverProcess = Start-Process -FilePath $studioExecutable -ArgumentList $serverArguments `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $storageDirectory 'server.stdout.log') `
        -RedirectStandardError (Join-Path $storageDirectory 'server.stderr.log') -PassThru
    Write-Host "Background server PID: $($serverProcess.Id)"
    return
}
& $studioExecutable start --data-dir $storageDirectory --port $port --internal-host 127.0.0.1 --no-browser
if ($LASTEXITCODE -ne 0) { throw 'Label Studio stopped with an error.' }
