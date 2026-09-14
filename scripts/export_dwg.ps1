# Map2CAD optional DWG export. Uses an existing licensed AutoCAD Core Console.
# Saves R2018, reopens actual DWG and exports DXF. Run compare_dxf.py afterward.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InputDxf,

    [Parameter(Mandatory)]
    [string]$OutputDwg,

    [Parameter(Mandatory)]
    [string]$RoundTripDxf,

    [Parameter(Mandatory)]
    [string]$CoreConsolePath,

    [switch]$Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Convert-ToCoreConsoleQuotedPath {
    param([Parameter(Mandatory)][string]$Path)
    '"' + $Path.Replace('"', '""') + '"'
}

function Invoke-CoreConsole {
    param(
        [Parameter(Mandatory)][string]$DrawingPath,
        [Parameter(Mandatory)][string]$ScriptText,
        [Parameter(Mandatory)][string]$Phase,
        [Parameter(Mandatory)][string]$JobDirectory,
        [Parameter(Mandatory)][string]$CoreConsole,
        [Parameter(Mandatory)][string]$ExpectedOutput
    )

    $scriptPath = Join-Path $JobDirectory "$Phase.scr"
    $stdoutPath = Join-Path $JobDirectory "$Phase.stdout.log"
    $stderrPath = Join-Path $JobDirectory "$Phase.stderr.log"
    [System.IO.File]::WriteAllText($scriptPath, $ScriptText, [System.Text.Encoding]::ASCII)

    $arguments = '/i "{0}" /s "{1}"' -f $DrawingPath, $scriptPath
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $CoreConsole
    $startInfo.Arguments = $arguments
    $startInfo.WorkingDirectory = Split-Path -Parent $CoreConsole
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    [void]$process.Start()
    # Consume both redirected streams while Core Console runs, avoiding pipe
    # deadlocks without sacrificing the hard timeout below.
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(120000)) {
        try { $process.Kill() } catch { }
        $process.WaitForExit()
        [System.IO.File]::WriteAllText($stdoutPath, $stdoutTask.Result, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($stderrPath, $stderrTask.Result, [System.Text.UTF8Encoding]::new($false))
        $process.Dispose()
        throw "AutoCAD Core Console $Phase exceeded 120 seconds. Log: $stdoutPath"
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    [System.IO.File]::WriteAllText($stdoutPath, $stdoutTask.Result, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText($stderrPath, $stderrTask.Result, [System.Text.UTF8Encoding]::new($false))
    $process.Dispose()
    $hasExpectedOutput = Test-Path -LiteralPath $ExpectedOutput -PathType Leaf
    if (($null -ne $exitCode) -and ($exitCode -ne 0)) {
        $details = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { '' }
        throw "AutoCAD Core Console $Phase failed with exit code $exitCode. Log: $stdoutPath`n$details"
    }
    if (-not $hasExpectedOutput) {
        $details = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -Raw -LiteralPath $stdoutPath } else { '' }
        throw "AutoCAD Core Console $Phase did not create expected output: $ExpectedOutput. ExitCode=$exitCode. Log: $stdoutPath`n$details"
    }

    [PSCustomObject]@{
        Phase = $Phase
        ExitCode = $exitCode
        Stdout = $stdoutPath
        Stderr = $stderrPath
        Script = $scriptPath
    }
}

function Get-ArtifactEvidence {
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$Signature
    )

    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    $evidence = [ordered]@{
        path = $item.FullName
        sizeBytes = [int64]$item.Length
        sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
    }
    if (-not [string]::IsNullOrWhiteSpace($Signature)) {
        $evidence.signature = $Signature
    }
    return $evidence
}

# Never overwrite an input, even when explicit output replacement is requested.
$inputFull = [System.IO.Path]::GetFullPath($InputDxf)
$outputFull = [System.IO.Path]::GetFullPath($OutputDwg)
$roundTripFull = [System.IO.Path]::GetFullPath($RoundTripDxf)
if (($inputFull -eq $outputFull) -or ($inputFull -eq $roundTripFull) -or ($outputFull -eq $roundTripFull)) {
    throw 'Paths must be distinct: input, DWG, and roundtrip DXF.'
}
$evidenceFull = $outputFull + '.conversion.json'
foreach ($protectedOutput in @($outputFull, $roundTripFull, $evidenceFull)) {
    if ((Test-Path -LiteralPath $protectedOutput) -and (-not $Overwrite)) {
        throw "Output already exists: $protectedOutput. Choose a new path or explicitly pass -Overwrite."
    }
}
if (([System.IO.Path]::GetExtension($inputFull) -ine '.dxf') -or
    ([System.IO.Path]::GetExtension($outputFull) -ine '.dwg') -or
    ([System.IO.Path]::GetExtension($roundTripFull) -ine '.dxf')) {
    throw 'Expected extensions: input .dxf, output .dwg, roundtrip .dxf.'
}
if (-not (Test-Path -LiteralPath $InputDxf -PathType Leaf)) {
    throw "Input DXF does not exist: $InputDxf"
}
if (-not (Test-Path -LiteralPath $CoreConsolePath -PathType Leaf)) {
    throw "AutoCAD Core Console does not exist: $CoreConsolePath"
}

$inputFull = (Resolve-Path -LiteralPath $InputDxf).Path
$outputFull = [System.IO.Path]::GetFullPath($OutputDwg)
$roundTripFull = [System.IO.Path]::GetFullPath($RoundTripDxf)

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFull), (Split-Path -Parent $roundTripFull) | Out-Null

$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) 'map2cad_dwg_staging'
if ($stagingRoot -match '[^\x00-\x7F]') { throw 'Core Console script staging needs an ASCII TEMP path; configure TEMP for this process only.' }
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
$jobDirectory = Join-Path $stagingRoot ("job_" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $jobDirectory | Out-Null

$stagedInput = Join-Path $jobDirectory 'source.dxf'
$stagedDwg = Join-Path $jobDirectory 'converted_r2018.dwg'
$stagedRoundTrip = Join-Path $jobDirectory 'reopened_export.dxf'
Copy-Item -LiteralPath $inputFull -Destination $stagedInput -Force

$saveAsScript = @(
    '_.FILEDIA', '0',
    '_.CMDDIA', '0',
    '_.AUDIT', '_Y',
    '_.SAVEAS', '2018', (Convert-ToCoreConsoleQuotedPath $stagedDwg),
    '_.QUIT'
) -join "`r`n"
$saveAsResult = Invoke-CoreConsole -DrawingPath $stagedInput -ScriptText $saveAsScript -Phase 'save_as_r2018' -JobDirectory $jobDirectory -CoreConsole $CoreConsolePath -ExpectedOutput $stagedDwg

$dwgBytes = [System.IO.File]::ReadAllBytes($stagedDwg)
if ($dwgBytes.Length -lt 6) {
    throw "Generated DWG is too small: $stagedDwg"
}
$dwgSignature = [System.Text.Encoding]::ASCII.GetString($dwgBytes[0..5])
if ($dwgSignature -ne 'AC1032') {
    throw "Unexpected DWG signature '$dwgSignature', expected AC1032 for R2018."
}

$dxfOutScript = @(
    '_.FILEDIA', '0',
    '_.CMDDIA', '0',
    '_.AUDIT', '_Y',
    '_.SAVEAS', 'dxf', '', (Convert-ToCoreConsoleQuotedPath $stagedRoundTrip),
    '_.QUIT'
) -join "`r`n"
$dxfOutResult = Invoke-CoreConsole -DrawingPath $stagedDwg -ScriptText $dxfOutScript -Phase 'reopen_audit_dxfout' -JobDirectory $jobDirectory -CoreConsole $CoreConsolePath -ExpectedOutput $stagedRoundTrip

Copy-Item -LiteralPath $stagedDwg -Destination $outputFull -Force
Copy-Item -LiteralPath $stagedRoundTrip -Destination $roundTripFull -Force

$outputDirectory = Split-Path -Parent $outputFull
$logDirectory = Join-Path $outputDirectory 'conversion_logs'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$runId = Split-Path -Leaf $jobDirectory
$inputAuditLogName = "$runId-save_as_r2018.stdout.log"
$reopenAuditLogName = "$runId-reopen_audit_dxfout.stdout.log"
Copy-Item -LiteralPath $saveAsResult.Stdout -Destination (Join-Path $logDirectory $inputAuditLogName) -Force
Copy-Item -LiteralPath $dxfOutResult.Stdout -Destination (Join-Path $logDirectory $reopenAuditLogName) -Force

$evidencePath = $evidenceFull
$evidence = [ordered]@{
    runUtc = (Get-Date).ToUniversalTime().ToString('o')
    coreConsole = [ordered]@{
        path = (Resolve-Path -LiteralPath $CoreConsolePath).Path
        stagingDirectory = $jobDirectory
    }
    inputDxf = Get-ArtifactEvidence -Path $inputFull
    dwg = Get-ArtifactEvidence -Path $outputFull -Signature $dwgSignature
    roundTripDxf = Get-ArtifactEvidence -Path $roundTripFull
    ac1032Verified = $true
    realDwgReopened = $true
    geometryComparison = 'pending: run compare_dxf.py on input and roundtrip before delivery'
    phases = @(
        [ordered]@{ name = $saveAsResult.Phase; exitCode = $saveAsResult.ExitCode },
        [ordered]@{ name = $dxfOutResult.Phase; exitCode = $dxfOutResult.ExitCode }
    )
    auditLogs = [ordered]@{
        inputDxf = (Join-Path 'conversion_logs' $inputAuditLogName)
        reopenedDwg = (Join-Path 'conversion_logs' $reopenAuditLogName)
    }
}
[System.IO.File]::WriteAllText(
    $evidencePath,
    ($evidence | ConvertTo-Json -Depth 8),
    [System.Text.UTF8Encoding]::new($false)
)


Write-Output "dwg=$outputFull"
Write-Output "roundtrip_dxf=$roundTripFull"
Write-Output "evidence=$evidencePath"
