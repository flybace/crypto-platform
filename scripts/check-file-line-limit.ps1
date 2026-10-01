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

$maxLines = 3000
$warningLines = 2400
$excludedDirectories = @(
    '.git',
    '.playwright-cli',
    'output',
    'node_modules',
    '.venv',
    'venv',
    '.pytest_cache',
    '__pycache__',
    '.mypy_cache',
    '.ruff_cache',
    'dist',
    'build',
    'coverage',
    'data',
    'runtime',
    'logs'
)
$textExtensions = @(
    '.md',
    '.txt',
    '.py',
    '.pyi',
    '.ps1',
    '.psm1',
    '.psd1',
    '.ts',
    '.tsx',
    '.js',
    '.jsx',
    '.vue',
    '.css',
    '.scss',
    '.html',
    '.json',
    '.jsonc',
    '.yaml',
    '.yml',
    '.toml',
    '.sql',
    '.sh',
    '.ini',
    '.cfg',
    '.conf',
    '.env',
    '.example'
)
$textFileNames = @(
    '.gitignore',
    '.gitkeep',
    'Dockerfile',
    'Makefile'
)

$rootPath = [System.IO.Path]::GetFullPath($Root)
if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) {
    throw "Scan root does not exist: $rootPath"
}

$files = Get-ChildItem -LiteralPath $rootPath -File -Recurse | Where-Object {
    $relativePath = $_.FullName.Substring($rootPath.Length).TrimStart('\', '/')
    $segments = $relativePath -split '[\\/]'
    $inExcludedDirectory = @($segments | Where-Object { $excludedDirectories -contains $_ }).Count -gt 0
    $isTextFile = ($textExtensions -contains $_.Extension.ToLowerInvariant()) -or ($textFileNames -contains $_.Name)
    (-not $inExcludedDirectory) -and $isTextFile
}

$violations = @()
$warnings = @()

foreach ($file in $files) {
    $lineCount = [System.IO.File]::ReadAllLines($file.FullName).Length
    $relativePath = $file.FullName.Substring($rootPath.Length).TrimStart('\', '/')
    '{0,6} {1}' -f $lineCount, $relativePath

    if ($lineCount -gt $maxLines) {
        $violations += [pscustomobject]@{
            Path = $relativePath
            Lines = $lineCount
        }
    }
    elseif ($lineCount -ge $warningLines) {
        $warnings += [pscustomobject]@{
            Path = $relativePath
            Lines = $lineCount
        }
    }
}

if ($warnings.Count -gt 0) {
    Write-Warning ("{0} file(s) reached the {1}-line split warning: {2}" -f $warnings.Count, $warningLines, (($warnings | ForEach-Object { "$($_.Path) ($($_.Lines))" }) -join ', '))
}

if ($violations.Count -gt 0) {
    $details = ($violations | ForEach-Object { "$($_.Path) ($($_.Lines) lines)" }) -join ', '
    Write-Error "File line limit exceeded: $details. Split the file before continuing."
    exit 1
}

Write-Output ("PASS: {0} text file(s) checked; no file exceeds {1} lines." -f @($files).Count, $maxLines)
