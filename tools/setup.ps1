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

function Need($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name is not installed. $hint"
    }
}

Need git "https://git-scm.com/download/win"
Need gh  "https://cli.github.com/"
Need py  "Install Python from the Microsoft Store, then re-run this."

# --- 1. GitHub auth -------------------------------------------------------
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Not logged in to GitHub. Run:  gh auth login"
}

$login = (& gh api user -q .login).Trim()
if (-not $Owner) { $Owner = $login }

# --- 2. Repo name ---------------------------------------------------------
if (-not $RepoName) {
    $cfg = Get-Content repo.config.json -Raw | ConvertFrom-Json
    if ($cfg.github.repo) { $RepoName = $cfg.github.repo } else { $RepoName = "family-apps" }
}

Write-Host "`nSetting up $Owner/$RepoName ...`n" -ForegroundColor Cyan

# --- 3. Write owner/repo into the config so every later run just works ---
# (python does the writing: PowerShell 5.1 would add a UTF-8 BOM and break the JSON)
& py tools\repogen.py set-repo --owner $Owner --repo $RepoName
if ($LASTEXITCODE -ne 0) { throw "could not update repo.config.json" }

# --- 4. git ---------------------------------------------------------------
if (-not (Test-Path .git)) { & git init -b main | Out-Null }

& git config user.name  (& gh api user -q .name 2>$null)
if (-not (& git config user.name)) { & git config user.name $login }
$email = (& gh api user -q .email 2>$null)
if (-not $email -or $email -eq "null") { $email = "$login@users.noreply.github.com" }
& git config user.email $email

& py tools\repogen.py sync | Out-Host
& git add -A
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { & git commit -m "Initial source layout" | Out-Host }

# --- 5. create + push -----------------------------------------------------
& git remote get-url origin *> $null
if ($LASTEXITCODE -ne 0) {
    $visibility = if ($Private) { "--private" } else { "--public" }
    & gh repo create "$Owner/$RepoName" $visibility --source . --remote origin --push --description "Feather app source"
    if ($LASTEXITCODE -ne 0) { throw "Could not create the repo. Does $Owner/$RepoName already exist?" }
} else {
    & git push -u origin main
}

# --- 6. GitHub Pages ------------------------------------------------------
$api = "repos/$Owner/$RepoName/pages"
& gh api $api *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "GitHub Pages already enabled."
} else {
    '{"source":{"branch":"main","path":"/"}}' | & gh api -X POST $api --input - | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not enable Pages automatically. Do it by hand:"
        Write-Warning "  Repo -> Settings -> Pages -> Source: Deploy from a branch -> main / (root)"
    } else {
        Write-Host "GitHub Pages enabled (branch: main / root)."
    }
}

$base = if ($RepoName -eq "$Owner.github.io") { "https://$Owner.github.io" } else { "https://$Owner.github.io/$RepoName" }
$url  = "$base/repo.json"

# --- 7. wait for the first publish ---------------------------------------
Write-Host "`nWaiting for the first Pages build (can take up to 2 minutes) ..."
$ok = $false
for ($i = 0; $i -lt 36; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15
        if ($resp.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 5
}

Write-Host ""
if ($ok) {
    Write-Host "Live:  $url" -ForegroundColor Green
} else {
    Write-Host "Pages is still building. Check again in a minute: $url" -ForegroundColor Yellow
}

Write-Host @"

Source URL to add in Feather:

    $url

Send FAMILY.md to everyone who needs it.
Next: publish an app with
    .\tools\publish.ps1 .\apps\YourApp.ipa -Publish
"@ -ForegroundColor Cyan
