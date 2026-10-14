# Run a command in the Mininet/Ryu container, with the project mounted at /project.
#   .\docker\sdn.ps1                      -> interactive bash
#   .\docker\sdn.ps1 mn --test pingall    -> any command
#   .\docker\sdn.ps1 build                -> (re)build the image
$Cmd = @($args)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot)   # project root

if ($Cmd.Count -gt 0 -and $Cmd[0] -eq 'build') {
    docker compose -f docker/compose.yaml build
    exit $LASTEXITCODE
}

# Docker's VM forgets kernel modules on restart; load openvswitch into it.
docker run --rm --privileged --pid=host alpine:3.20 nsenter -t 1 -m -- modprobe openvswitch
if ($LASTEXITCODE -ne 0) { Write-Error 'Could not load the openvswitch kernel module in Docker' }

if ($Cmd.Count -eq 0) { $Cmd = @('bash') }
docker compose -f docker/compose.yaml run --rm sdn @Cmd
exit $LASTEXITCODE
