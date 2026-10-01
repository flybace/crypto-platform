[CmdletBinding()]
param(
    [string]$Root
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($Root)) {
    $scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
    $Root = Split-Path -Parent $scriptDirectory
}

$rootPath = [System.IO.Path]::GetFullPath($Root)
$planPath = Join-Path $rootPath 'docs\PROJECT_PLAN.md'
$mindmapPath = Join-Path $rootPath 'docs\PROJECT_MINDMAP.md'

if (-not (Test-Path -LiteralPath $planPath -PathType Leaf)) {
    throw "Plan file does not exist: $planPath"
}
if (-not (Test-Path -LiteralPath $mindmapPath -PathType Leaf)) {
    throw "Mindmap file does not exist: $mindmapPath"
}

$planSections = [ordered]@{}
foreach ($line in [System.IO.File]::ReadAllLines($planPath)) {
    if ($line -match '^## (?<number>\d{2})\. (?<title>.+?)\s*$') {
        $number = $Matches['number']
        if ($planSections.Contains($number)) {
            throw "Duplicate plan section: $number"
        }
        $planSections[$number] = $Matches['title'].Trim()
    }
}

$mindmapSections = [ordered]@{}
foreach ($line in [System.IO.File]::ReadAllLines($mindmapPath)) {
    if ($line -match '^ {4}(?<number>\d{2}) (?<title>.+?)\s*$') {
        $number = $Matches['number']
        if ($mindmapSections.Contains($number)) {
            throw "Duplicate mindmap section: $number"
        }
        $mindmapSections[$number] = $Matches['title'].Trim()
    }
}

$errors = [System.Collections.Generic.List[string]]::new()
foreach ($number in $planSections.Keys) {
    if (-not $mindmapSections.Contains($number)) {
        $errors.Add("Mindmap is missing section $number")
        continue
    }
    $planTitle = $planSections[$number]
    $mindmapTitle = $mindmapSections[$number]
    if ($planTitle -ne $mindmapTitle) {
        $errors.Add("Title mismatch for section ${number}: plan='$planTitle'; mindmap='$mindmapTitle'")
    }
}
foreach ($number in $mindmapSections.Keys) {
    if (-not $planSections.Contains($number)) {
        $errors.Add("Mindmap contains unknown section $number")
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    exit 1
}

Write-Output ("PASS: {0} plan sections match the mindmap." -f $planSections.Count)
