<#
.SYNOPSIS
  One-time setup: create the GitHub repo, push it, and turn on GitHub Pages.

.EXAMPLE
  .\tools\setup.ps1 -RepoName family-apps
#>
[CmdletBinding()]
param(
    [string]$RepoName = "",
    [string]$Owner = "",
    [switch]$Private
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# PowerShell 5.1 turns a native command's stderr into a terminating error,
# so every call that is allowed to fail runs with a quieter preference.
function Invoke-AllowedFail {
    param([scriptblock]$Block)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try { & $Block } finally { $ErrorActionPreference = $old }
}

function Need($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name is not installed. $hint"
    }
}

Need git "https://git-scm.com/download/win"
Need gh  "https://cli.github.com/"
Need py  "Install Python, then re-run this."

# --- 1. GitHub auth -------------------------------------------------------
Invoke-AllowedFail { & gh auth status *> $null }
if ($LASTEXITCODE -ne 0) { throw "Not logged in to GitHub. Run:  gh auth login" }

$login = (& gh api user -q .login).Trim()
if (-not $Owner) { $Owner = $login }

# --- 2. Repo name ---------------------------------------------------------
if (-not $RepoName) {
    $cfg = Get-Content repo.config.json -Raw | ConvertFrom-Json
    if ($cfg.github.repo) { $RepoName = $cfg.github.repo } else { $RepoName = "family-apps" }
}

Write-Host "`nSetting up $Owner/$RepoName ...`n" -ForegroundColor Cyan

# --- 3. Record owner/repo (python writes it: keeps the JSON BOM-free) -----
& py tools\repogen.py set-repo --owner $Owner --repo $RepoName
if ($LASTEXITCODE -ne 0) { throw "could not update repo.config.json" }

# --- 4. git ---------------------------------------------------------------
if (-not (Test-Path .git)) { & git init -b main | Out-Null }

$name = (Invoke-AllowedFail { & gh api user -q .name })
if (-not $name -or $name -eq "null") { $name = $login }
& git config user.name $name | Out-Null

$email = (Invoke-AllowedFail { & gh api user -q .email })
if (-not $email -or $email -eq "null") { $email = "$login@users.noreply.github.com" }
& git config user.email $email | Out-Null

& py tools\repogen.py sync | Out-Host

& git add -A
Invoke-AllowedFail { & git diff --cached --quiet }
if ($LASTEXITCODE -ne 0) { & git commit -m "Initial source layout" | Out-Host }

# --- 5. create + push -----------------------------------------------------
$remotes = Invoke-AllowedFail { & git remote }
if ($remotes -notcontains "origin") {
    $visibility = if ($Private) { "--private" } else { "--public" }
    $create = Invoke-AllowedFail {
        & gh repo create "$Owner/$RepoName" $visibility --source . --remote origin --push `
            --description "Feather app source"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host $create
        throw "Could not create $Owner/$RepoName - does it already exist?"
    }
    Write-Host "Repo created and pushed."
} else {
    & git push -u origin main
}

# --- 6. GitHub Pages ------------------------------------------------------
$api = "repos/$Owner/$RepoName/pages"
$pagesInfo = Invoke-AllowedFail { & gh api $api }
$pagesOk = $LASTEXITCODE -eq 0

if ($pagesOk) {
    Write-Host "GitHub Pages is already enabled."
} else {
    Invoke-AllowedFail {
        '{"source":{"branch":"main","path":"/"}}' | & gh api -X POST $api --input - | Out-Null
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "GitHub Pages enabled (main / root)."
    } else {
        Write-Warning "Could not enable Pages automatically. Do it by hand:"
        Write-Warning "  Repo -> Settings -> Pages -> Source: Deploy from a branch -> main / (root)"
    }
}

$base = if ($RepoName -eq "$Owner.github.io") { "https://$Owner.github.io" }
        else { "https://$Owner.github.io/$RepoName" }
$url  = "$base/repo.json"

# --- 7. wait for the first build -----------------------------------------
Write-Host "`nWaiting for the first Pages build (can take up to 2 minutes) ..."
$live = $false
for ($i = 0; $i -lt 36; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
        if ($resp.StatusCode -eq 200) { $live = $true; break }
    } catch { }
    Start-Sleep -Seconds 5
}

if ($live) { Write-Host "`nLIVE:  $url" -ForegroundColor Green }
else       { Write-Host "`nStill building. Check again shortly: $url" -ForegroundColor Yellow }

Write-Host @"

Source URL to add in Feather:

    $url

Send FAMILY.md to everyone who needs it.
Publish an app with:
    .\tools\publish.ps1 .\apps\YourApp.ipa -Publish
"@ -ForegroundColor Cyan
