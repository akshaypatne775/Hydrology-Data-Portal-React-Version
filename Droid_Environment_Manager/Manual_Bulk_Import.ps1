param(
    [string]$ProjectId = "",
    [string]$SourceFolder = "",
    [ValidateSet("copc", "cog", "tiles", "las", "open", "")]
    [string]$Mode = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ProjectData = Join-Path $Root "Project_Data"
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$BulkLasScript = Join-Path $Root "backend\scripts\bulk_pointcloud_import.py"

function Get-ProjectDataRoot {
    $envFile = Join-Path $Root "backend\.env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*LOCAL_DATA_PATH\s*=\s*(.+)\s*$') {
                $value = $Matches[1].Trim().Trim('"').Trim("'")
                if ($value) { return $value }
            }
        }
    }
    return $ProjectData
}

function Ensure-ProjectId {
    param([string]$Value)
    if ($Value) { return $Value.Trim() }
    return (Read-Host "Enter project id (example: proj_0c7e667eba610b2a)").Trim()
}

function Ensure-SourceFolder {
    param([string]$Value, [string]$Prompt)
    if ($Value) { return $Value.Trim('"') }
    return (Read-Host $Prompt).Trim('"')
}

function Infer-RasterType {
    param([string]$Name)
    $lower = $Name.ToLower()
    if ($lower -match "dtm|dem") { return "dtm" }
    if ($lower -match "dsm") { return "dsm" }
    if ($lower -match "ortho") { return "ortho" }
    return "ortho"
}

function Copy-CopcFile {
    param([string]$ProjectRoot, [System.IO.FileInfo]$File)
    $folderName = [System.IO.Path]::GetFileNameWithoutExtension($File.Name)
    $folderName = ($folderName -replace '[^A-Za-z0-9._ -]', '-').Trim('-')
    if (-not $folderName) { $folderName = "pointcloud" }
    $targetDir = Join-Path $ProjectRoot "exports\pointclouds\$folderName"
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    $targetFile = Join-Path $targetDir "output.copc.laz"
    Copy-Item -LiteralPath $File.FullName -Destination $targetFile -Force
    Set-Content -Path (Join-Path $targetDir ".viewer_type.txt") -Value "copc" -Encoding UTF8
    Set-Content -Path (Join-Path $targetDir ".source_name.txt") -Value $File.Name -Encoding UTF8
    Write-Host "  COPC -> $targetFile"
}

function Copy-CogFile {
    param([string]$ProjectRoot, [System.IO.FileInfo]$File)
    $type = Infer-RasterType $File.Name
    $targetDir = Join-Path $ProjectRoot "processed\$type"
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    $base = [System.IO.Path]::GetFileNameWithoutExtension($File.Name)
    if ($File.Name -match '\.cog\.(tif|tiff)$') {
        $targetName = $File.Name
    } else {
        $ext = [System.IO.Path]::GetExtension($File.Name)
        $targetName = "$base.cog$ext"
    }
    $targetFile = Join-Path $targetDir $targetName
    Copy-Item -LiteralPath $File.FullName -Destination $targetFile -Force
    Write-Host "  COG -> $targetFile"
}

function Copy-TileFolder {
    param([string]$ProjectRoot, [System.IO.DirectoryInfo]$Folder)
    $type = Infer-RasterType $Folder.Name
    $targetDir = Join-Path $ProjectRoot "processed\$type\$($Folder.Name)"
    if (Test-Path $targetDir) {
        Remove-Item -LiteralPath $targetDir -Recurse -Force
    }
    Copy-Item -LiteralPath $Folder.FullName -Destination $targetDir -Recurse -Force
    Write-Host "  TILES -> $targetDir"
}

$dataRoot = Get-ProjectDataRoot
Write-Host ""
Write-Host "Droid Cloud - Manual Bulk Import"
Write-Host "Data root: $dataRoot"
Write-Host ""

if (-not $Mode) {
    Write-Host "Choose mode:"
    Write-Host "  1) copc   - already converted COPC (.copc.laz)"
    Write-Host "  2) cog    - already converted COG (.tif/.tiff)"
    Write-Host "  3) tiles  - XYZ tile folders (0/, 1/, ... png)"
    Write-Host "  4) las    - bulk LAS/LAZ (auto COPC conversion via PDAL)"
    Write-Host "  5) open   - open project processed folder"
    $choice = Read-Host "Enter 1-5"
    switch ($choice) {
        "1" { $Mode = "copc" }
        "2" { $Mode = "cog" }
        "3" { $Mode = "tiles" }
        "4" { $Mode = "las" }
        "5" { $Mode = "open" }
        default { throw "Invalid choice." }
    }
}

$ProjectId = Ensure-ProjectId $ProjectId
$projectRoot = Join-Path $dataRoot "projects\$ProjectId"
if (-not (Test-Path $projectRoot)) {
    New-Item -ItemType Directory -Force -Path $projectRoot | Out-Null
    Write-Host "Created project folder: $projectRoot"
}

if ($Mode -eq "open") {
    $processed = Join-Path $projectRoot "processed"
    New-Item -ItemType Directory -Force -Path $processed | Out-Null
    Start-Process explorer.exe $processed
    Write-Host "Opened: $processed"
    Write-Host "Tip: COPC files go in exports\pointclouds\, not processed\"
    exit 0
}

if ($Mode -eq "las") {
    $SourceFolder = Ensure-SourceFolder $SourceFolder "Enter folder containing LAS/LAZ files"
    if (-not (Test-Path $BulkLasScript)) { throw "Missing script: $BulkLasScript" }
    if (-not (Test-Path $BackendPython)) { throw "Missing backend venv python: $BackendPython" }
    & $BackendPython $BulkLasScript --project-id $ProjectId --source $SourceFolder
    exit $LASTEXITCODE
}

$SourceFolder = Ensure-SourceFolder $SourceFolder "Enter source folder"
$sourcePath = Resolve-Path $SourceFolder

switch ($Mode) {
    "copc" {
        $files = Get-ChildItem -LiteralPath $sourcePath -File -Filter "*.copc.laz"
        if (-not $files.Count) { throw "No .copc.laz files found in $sourcePath" }
        foreach ($file in $files) { Copy-CopcFile $projectRoot $file }
    }
    "cog" {
        $files = Get-ChildItem -LiteralPath $sourcePath -File | Where-Object {
            $_.Extension -in ".tif", ".tiff"
        }
        if (-not $files.Count) { throw "No .tif/.tiff files found in $sourcePath" }
        foreach ($file in $files) { Copy-CogFile $projectRoot $file }
    }
    "tiles" {
        $folders = Get-ChildItem -LiteralPath $sourcePath -Directory
        if (-not $folders.Count) { throw "No subfolders found in $sourcePath" }
        foreach ($folder in $folders) { Copy-TileFolder $projectRoot $folder }
    }
}

Write-Host ""
Write-Host "Files copied."
Write-Host "Next step: open portal -> Data Catalog -> Sync Manual Folders"
Write-Host "Project path: $projectRoot"
