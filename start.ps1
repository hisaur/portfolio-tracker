<#
.SYNOPSIS
    1-Command End-to-End Runner for Ephemeral Ghostfolio Sync Pipeline
.DESCRIPTION
    1. Validates host Google Drive CSV
    2. Spins up ephemeral Ghostfolio, Postgres, and Redis containers
    3. Waits for Ghostfolio readiness
    4. Executes sync-job to auto-provision user, map CSV trades, and import into Ghostfolio
    5. Opens Ghostfolio in your default browser
#>

param (
    [switch]$Watch,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  🚀 GHOSTFOLIO EPHEMERAL PIPELINE: 1-COMMAND RUNNER " -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Ephemeral Secret Generation (In-Memory Only - Zero Secrets on Disk)
if (-not $env:POSTGRES_PASSWORD) {
    $env:POSTGRES_PASSWORD = [System.Guid]::NewGuid().ToString("N")
}
if (-not $env:JWT_SECRET_KEY) {
    $env:JWT_SECRET_KEY = [System.Guid]::NewGuid().ToString("N") + [System.Guid]::NewGuid().ToString("N")
}
if (-not $env:ACCESS_TOKEN_SALT) {
    $env:ACCESS_TOKEN_SALT = [System.Guid]::NewGuid().ToString("N")
}

# 2. Check for optional local overrides (.env)
$EnvFile = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir ".env.example"

if (-not (Test-Path $EnvFile) -and (Test-Path $EnvExample)) {
    Copy-Item $EnvExample $EnvFile
}

# 2. Check Local Synced CSV
$CsvHostPath = $env:LOCAL_CSV_PATH
if (-not $CsvHostPath -and (Test-Path $EnvFile)) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*LOCAL_CSV_PATH\s*=\s*(.+)$') {
            $CsvHostPath = $matches[1].Trim('"').Trim("'")
        }
    }
}
if (-not $CsvHostPath) {
    $CsvHostPath = "G:\My Drive\portfolio_docker_sync.csv"
}

if (Test-Path $CsvHostPath) {
    $LineCount = (Get-Content $CsvHostPath | Measure-Object -Line).Lines
    Write-Host "[OK] Synced Google Drive CSV found: $CsvHostPath ($LineCount lines)" -ForegroundColor Green
} else {
    Write-Host "[WARNING] Synced CSV not found at $CsvHostPath." -ForegroundColor Yellow
    Write-Host "          Ensure Google Drive for Desktop is running and syncing," -ForegroundColor Yellow
    Write-Host "          or configure LOCAL_CSV_PATH in .env." -ForegroundColor Yellow
}

# 3. Dry-Run Mode check
if ($DryRun) {
    Write-Host "[INFO] Executing Dry-Run..." -ForegroundColor Cyan
    $env:CSV_FILE_PATH = $CsvHostPath
    python (Join-Path $ScriptDir "sync-job\sync.py") --dry-run
    exit 0
}

# 4. Check Docker availability
try {
    docker --version | Out-Null
} catch {
    Write-Host "[ERROR] Docker is not found in PATH or Docker Desktop is not running." -ForegroundColor Red
    Write-Host "        Please start Docker Desktop and run this script again." -ForegroundColor Yellow
    exit 1
}

# 5. Spin up ephemeral core services (Postgres, Redis, Ghostfolio)
Write-Host "`n[1/4] Starting ephemeral services (PostgreSQL, Redis, Ghostfolio)..." -ForegroundColor Cyan
docker compose -f (Join-Path $ScriptDir "docker-compose.yml") up -d postgres redis ghostfolio

# 6. Wait for Ghostfolio HTTP healthcheck
Write-Host "`n[2/4] Waiting for Ghostfolio API to become healthy at http://localhost:3333..." -ForegroundColor Cyan
$MaxRetries = 40
$Retries = 0
$IsHealthy = $false

while ($Retries -lt $MaxRetries) {
    Start-Sleep -Seconds 3
    $Retries++
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:3333/api/v1/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            $IsHealthy = $true
            break
        }
    } catch {
        Write-Host -NoNewline "."
    }
}

if (-not $IsHealthy) {
    Write-Host "`n[ERROR] Ghostfolio failed to respond healthy within 120 seconds." -ForegroundColor Red
    Write-Host "        Check container logs using: docker compose logs ghostfolio" -ForegroundColor Yellow
    exit 1
}

Write-Host "`n[OK] Ghostfolio is healthy and accepting connections!" -ForegroundColor Green

# 7. Execute Ingestion Sidecar
Write-Host "`n[3/4] Ingesting portfolio trades into Ghostfolio..." -ForegroundColor Cyan
if ($Watch) {
    Write-Host "[INFO] Starting sidecar in continuous WATCH mode..." -ForegroundColor Cyan
    docker compose -f (Join-Path $ScriptDir "docker-compose.yml") up -d sync-job
} else {
    docker compose -f (Join-Path $ScriptDir "docker-compose.yml") run --rm sync-job
}

# 8. Open Dashboard in Browser
Write-Host "`n[4/4] Launching Ghostfolio Dashboard..." -ForegroundColor Cyan
Start-Process "http://localhost:3333"

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  ✨ PIPELINE COMPLETE! GHOSTFOLIO IS READY " -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:3333" -ForegroundColor Cyan
Write-Host "  Source:    $CsvHostPath" -ForegroundColor White
Write-Host "-----------------------------------------------------------------" -ForegroundColor Gray
Write-Host "  Generated Credentials (printed in sidecar log above):" -ForegroundColor White
Write-Host "  - GHOSTFOLIO SECURITY TOKEN (to sign into Web UI)" -ForegroundColor Yellow
Write-Host "  - GHOSTFOLIO BEARER API KEY (for REST API / curl)" -ForegroundColor Yellow
Write-Host "-----------------------------------------------------------------" -ForegroundColor Gray
Write-Host "  To stop and wipe this ephemeral stack when finished viewing:   " -ForegroundColor Yellow
Write-Host "  run: .\cleanup.ps1                                            " -ForegroundColor Yellow
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
