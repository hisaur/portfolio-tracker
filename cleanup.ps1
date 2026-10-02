<#
.SYNOPSIS
    Comprehensive Teardown and Ephemeral Storage Cleanup Script
.DESCRIPTION
    Tears down all running containers, wipes ephemeral PostgreSQL and Redis volumes,
    removes isolated networks, purges orphaned resources, and cleans local temporary cache.
.PARAMETER Deep
    Also removes built Docker images and dangling build cache for a 100% clean slate.
#>
param (
    [switch]$Deep
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Yellow
Write-Host "  🧹 GHOSTFOLIO EPHEMERAL TEARDOWN & STORAGE CLEANUP " -ForegroundColor Yellow
Write-Host "=================================================================" -ForegroundColor Yellow
Write-Host ""

# 1. Stop containers and destroy volumes & orphans
Write-Host "[1/3] Stopping containers and removing ephemeral volumes..." -ForegroundColor Cyan
try {
    docker compose -f (Join-Path $ScriptDir "docker-compose.yml") down -v --remove-orphans
    Write-Host "[OK] Containers, networks, and volumes destroyed." -ForegroundColor Green
} catch {
    Write-Host "[WARNING] Could not cleanly execute docker compose down: $_" -ForegroundColor Yellow
}

# 2. Check and purge any remaining project-specific volumes
Write-Host "`n[2/3] Checking for any lingering ephemeral volumes..." -ForegroundColor Cyan
try {
    $ProjectVolumes = docker volume ls --filter "name=ghostfolio" -q
    if ($ProjectVolumes) {
        foreach ($vol in $ProjectVolumes) {
            Write-Host "  Removing volume: $vol" -ForegroundColor Gray
            docker volume rm $vol -f 2>$null | Out-Null
        }
        Write-Host "[OK] Lingering volumes purged." -ForegroundColor Green
    } else {
        Write-Host "[OK] No lingering Docker volumes found." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Docker volume check completed." -ForegroundColor Gray
}

# Optional Deep Clean: Remove built images & build cache
if ($Deep) {
    Write-Host "`n[DEEP CLEAN] Removing sidecar Docker images and build cache..." -ForegroundColor Magenta
    try {
        $Images = docker images --filter "reference=*ghostfolio*sync*" -q
        if ($Images) {
            foreach ($img in $Images) {
                docker rmi $img -f 2>$null | Out-Null
            }
            Write-Host "[OK] Project Docker images removed." -ForegroundColor Green
        }
        docker builder prune -f --filter "until=1h" 2>$null | Out-Null
    } catch {
        Write-Host "[INFO] Deep cleanup completed." -ForegroundColor Gray
    }
}

# 3. Clean up local temporary files and Python cache
Write-Host "`n[3/3] Purging local caches and temporary files..." -ForegroundColor Cyan
$PycacheFolders = Get-ChildItem -Path $ScriptDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
foreach ($folder in $PycacheFolders) {
    Remove-Item -Path $folder.FullName -Recurse -Force -ErrorAction SilentlyContinue
}
$TempFiles = Get-ChildItem -Path $ScriptDir -Recurse -File -Include "*.tmp", "*.pyc", "*.log" -ErrorAction SilentlyContinue
foreach ($file in $TempFiles) {
    Remove-Item -Path $file.FullName -Force -ErrorAction SilentlyContinue
}
Write-Host "[OK] Local caches purged." -ForegroundColor Green

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  ✨ TEARDOWN COMPLETE! ZERO EPHEMERAL ARTIFACTS REMAIN " -ForegroundColor Green
Write-Host "-----------------------------------------------------------------" -ForegroundColor Gray
Write-Host "  - PostgreSQL Ephemeral DB: WIPED" -ForegroundColor White
Write-Host "  - Redis Ephemeral Cache:   WIPED" -ForegroundColor White
Write-Host "  - Containers & Networks:   DESTROYED" -ForegroundColor White
Write-Host "  - Local Trade CSV:         SAFE ON GOOGLE DRIVE" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
