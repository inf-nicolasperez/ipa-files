<#
.SYNOPSIS
  Publish an .ipa: upload it to a GitHub Release and add it to repo.json.

.EXAMPLE
  .\tools\publish.ps1 .\apps\Spotify_9.1.78.ipa -Publish

.EXAMPLE
  .\tools\publish.ps1 .\apps\App.ipa -Tag myapp-1.2 -Publish
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Ipa,

    # Commit and push repo.json when done.
    [switch]$Publish,

    # Put the file under this release tag instead of the auto-generated one.
    [string]$Tag,

    # Use your own PNG instead of the icon inside the IPA.
    [string]$Icon,

    # Preview everything without uploading or writing.
    [switch]$DryRun,

    # Skip the GitHub upload entirely (repo.json only).
    [switch]$NoUpload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exe = if (Get-Command py -ErrorAction SilentlyContinue) { "py" }
       elseif (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" }
       else { "python" }

$argv = @("tools\repogen.py", "add", $Ipa)
if ($Tag)      { $argv += @("--tag", $Tag) }
if ($Icon)     { $argv += @("--icon", $Icon) }
if ($Publish)  { $argv += "--publish" }
if ($DryRun)   { $argv += "--dry-run" }
if ($NoUpload) { $argv += "--no-upload" }

& $exe @argv
exit $LASTEXITCODE
