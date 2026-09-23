$ErrorActionPreference = "Stop"

$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path (Join-Path $projectRoot ".env"))) {
    throw "Configurazione locale mancante: .env. Creala con: Copy-Item .env.example .env. Poi configura i file richiesti in docker/secrets (vedi README.md)."
}

$requiredSecrets = "postgres_password", "database_url", "app_secret_key", "admin_password"
$missingSecrets = $requiredSecrets | Where-Object { -not (Test-Path (Join-Path $projectRoot "docker/secrets/$_")) }
if ($missingSecrets) {
    throw "Secret Docker mancanti in docker/secrets: $($missingSecrets -join ', '). Segui la sezione 'Avvio rapido' del README.md per crearli."
}

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
