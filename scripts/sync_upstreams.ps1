[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $PSScriptRoot "..\upstream")
)

$ErrorActionPreference = "Stop"
$Destination = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $Destination -Force | Out-Null

$repositories = @(
    @{ Name = "TES5Edit"; Url = "https://github.com/TES5Edit/TES5Edit.git" },
    @{ Name = "Vortex"; Url = "https://github.com/Nexus-Mods/Vortex.git" }
)

foreach ($repository in $repositories) {
    $target = Join-Path $Destination $repository.Name
    if (Test-Path -LiteralPath (Join-Path $target ".git")) {
        git -C $target pull --ff-only
    }
    else {
        git clone --depth 1 $repository.Url $target
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Git operation failed for $($repository.Name)"
    }
}

