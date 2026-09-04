param(
    [switch]$Execute,
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$ArchiveDir = 'dead_code_archive'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$files = @(
    'batch_image_processor.py',
    'launch_enhanced_chatbot.py',
    'feedback_system_integration.py',
    'integrated_feedback_system.py',
    'feedback_analysis_tab_crashsafe.py',
    'enhanced_feedback_analysis_tab.py',
    'smart_fusion_engine.py',
    'generic_visualization_tool.py',
    'response_feedback_widget.py',
    'smart_feedback_widget.py',
    'feedback_integration_code.py',
    'code_executor_engine.py',
    'pdf_readability_checker.py',
    'network_visualizer.py',
    'diagram_quality_validator.py',
    'intelligent_image_validator.py',
    'internet_image_integrator.py',
    'gpu_optimizer.py',
    'ministral_reasoning_optimizer.py',
    'advanced_pdf_processor.py',
    'pydantic_migration_adapter.py',
    'monitoring_dashboard.py',
    'structured_data_extractor.py',
    'llm_feedback_analyzer.py'
)

function Test-RgAvailable {
    $cmd = Get-Command rg -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw 'ripgrep (rg) ist erforderlich, aber nicht installiert oder nicht im PATH.'
    }
}

function Get-ReferenceCount {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$FileName
    )

    $module = [System.IO.Path]::GetFileNameWithoutExtension($FileName)
    $escapedName = [Regex]::Escape($FileName)
    $escapedModule = [Regex]::Escape($module)
    $pattern = "(^|[^A-Za-z0-9_])($escapedName|$escapedModule)([^A-Za-z0-9_]|$)"

    $raw = rg -n -P --hidden --glob '*.py' --glob '*.ps1' -e $pattern $WorkspaceRoot 2>$null
    if (-not $raw) {
        return @{ Count = 0; Hits = @() }
    }

    # Deterministische Nachfilterung (rg-Glob-Excludes sind bei absoluten
    # Suchpfaden unter Windows nicht zuverlaessig):
    # - Archiv, Backups, .bak_-Kopien, .git und venvs sind keine Live-Nutzung
    # - Selbstreferenzen der Kandidatendatei (eigener Docstring) ebenfalls nicht
    # - Dieses Skript selbst (die Kandidatenliste) zaehlt nicht als Nutzung
    $selfPath = Join-Path $WorkspaceRoot $FileName
    $scriptPath = $PSCommandPath
    $hits = @($raw | Where-Object {
        $line = $_
        $filePart = ($line -split ':\d+:', 2)[0]
        -not (
            $filePart -like '*\dead_code_archive\*' -or
            $filePart -like '*\backups\*' -or
            $filePart -like '*.bak_*' -or
            $filePart -like '*\.git\*' -or
            $filePart -like '*\venv_*' -or
            $filePart -ieq $selfPath -or
            $filePart -ieq $scriptPath
        )
    })
    return @{ Count = $hits.Count; Hits = $hits }
}

Test-RgAvailable

$rootFull = (Resolve-Path -Path $Root).Path
$dest = Join-Path $rootFull $ArchiveDir
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$report = [System.Collections.Generic.List[object]]::new()
$moved = [System.Collections.Generic.List[string]]::new()
$blocked = [System.Collections.Generic.List[string]]::new()

foreach ($f in $files) {
    $src = Join-Path $rootFull $f

    if (-not (Test-Path $src)) {
        $report.Add([PSCustomObject]@{
            file = $f
            exists = $false
            reference_count = 0
            decision = 'missing'
            action = 'none'
        }) | Out-Null
        continue
    }

    $ref = Get-ReferenceCount -WorkspaceRoot $rootFull -FileName $f
    $decision = if ($ref.Count -gt 0) { 'blocked_active_references' } else { 'eligible' }

    if ($ref.Count -gt 0) {
        $blocked.Add($f) | Out-Null
        $report.Add([PSCustomObject]@{
            file = $f
            exists = $true
            reference_count = $ref.Count
            decision = $decision
            action = 'keep'
            sample_hits = ($ref.Hits | Select-Object -First 5)
        }) | Out-Null
        continue
    }

    if ($Execute) {
        Move-Item -Path $src -Destination $dest -Force
        $moved.Add($f) | Out-Null
        $action = 'moved'
    } else {
        $action = 'dry_run'
    }

    $report.Add([PSCustomObject]@{
        file = $f
        exists = $true
        reference_count = 0
        decision = $decision
        action = $action
    }) | Out-Null
}

$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$reportPath = Join-Path $dest ("archive_dead_code_report_" + $ts + ".json")
$report | ConvertTo-Json -Depth 6 | Out-File -FilePath $reportPath -Encoding utf8

Write-Host "=== archive_dead_code.ps1 summary ==="
if ($Execute) {
    Write-Host "Mode: EXECUTE"
} else {
    Write-Host "Mode: DRY-RUN"
}
Write-Host "Workspace: $rootFull"
Write-Host "Archive:   $dest"
Write-Host "Moved:     $($moved.Count)"
Write-Host "Blocked:   $($blocked.Count)"
Write-Host "Report:    $reportPath"

if ($blocked.Count -gt 0) {
    Write-Host ""
    Write-Host "Blocked files (active references found):"
    $blocked | ForEach-Object { Write-Host " - $_" }
}

if (-not $Execute) {
    Write-Host ""
    Write-Host "No files moved (dry-run). Re-run with -Execute to archive only eligible files."
}