<#
    DPCompat unified development commands for Windows (PowerShell).

    This script mirrors the GNU Makefile targets so Windows developers do not need
    GNU Make or WSL.  Every target runs the same quality gates as `make <target>`:

        .\scripts\build.ps1 sync        # uv sync --all-groups
        .\scripts\build.ps1 format      # ruff format + ruff check --fix
        .\scripts\build.ps1 lint        # ruff format --check + ruff check
        .\scripts\build.ps1 typecheck   # strict mypy
        .\scripts\build.ps1 test        # pytest -q
        .\scripts\build.ps1 coverage    # pytest with coverage report
        .\scripts\build.ps1 check       # lint + typecheck + test
        .\scripts\build.ps1 smoke       # CLI smoke over examples/simple_pack
        .\scripts\build.ps1 build       # check + uv build
        .\scripts\build.ps1 clean       # remove known generated artifacts

    Cleanup is intentionally delegated to scripts/clean.py so PowerShell and the
    Makefile share one reviewed deletion list instead of duplicated shell logic.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("help", "sync", "format", "lint", "typecheck", "test", "test-verbose", "coverage", "check", "build", "smoke", "clean")]
    [string]$Target = "help",

    [string]$Uv = "uv"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Invoke-Uv {
    param([string[]]$Arguments)
    & $Uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

switch ($Target) {
    "help" {
        Get-Help $PSCommandPath -Detailed
    }
    "sync" {
        Invoke-Uv @("sync", "--all-groups")
    }
    "format" {
        Invoke-Uv @("run", "ruff", "format", ".")
        Invoke-Uv @("run", "ruff", "check", "--fix", ".")
    }
    "lint" {
        Invoke-Uv @("run", "ruff", "format", "--check", ".")
        Invoke-Uv @("run", "ruff", "check", ".")
    }
    "typecheck" {
        Invoke-Uv @("run", "mypy")
    }
    "test" {
        Invoke-Uv @("run", "pytest", "-q")
    }
    "test-verbose" {
        Invoke-Uv @("run", "pytest", "-vv")
    }
    "coverage" {
        Invoke-Uv @("run", "pytest", "--cov=dpcompat", "--cov-report=term-missing")
    }
    "check" {
        & $PSCommandPath lint
        & $PSCommandPath typecheck
        & $PSCommandPath test
    }
    "build" {
        & $PSCommandPath check
        Invoke-Uv @("build")
    }
    "smoke" {
        Invoke-Uv @("run", "dpcompat", "versions")
        Invoke-Uv @("run", "dpcompat", "inspect", "examples/simple_pack")
    }
    "clean" {
        Invoke-Uv @("run", "python", "scripts/clean.py")
    }
}
