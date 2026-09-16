$ErrorActionPreference = "Stop"

$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-DockerReady {
    try {
        docker version --format '{{.Server.Version}}' *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

if (-not (Test-DockerReady)) {
    if (-not (Test-Path $dockerDesktop)) {
        throw "Docker Desktop non trovato: $dockerDesktop"
    }

    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath $dockerDesktop
    }

    Write-Host "Attendo l'avvio di Docker Desktop..."
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-DockerReady)) {
        if ((Get-Date) -ge $deadline) {
            throw "Docker Desktop non e disponibile dopo 3 minuti."
        }
        Start-Sleep -Seconds 3
    }
}

Set-Location $projectRoot
Write-Host "Docker pronto. Avvio Street Skate su http://localhost:5000"
docker compose up --build
