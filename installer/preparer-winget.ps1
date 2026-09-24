<#
.SYNOPSIS
    Génère les manifestes winget pour Ruche à partir de l'installateur compilé.

.DESCRIPTION
    Calcule l'empreinte SHA-256 de l'installateur et écrit l'arborescence de
    manifestes attendue par le dépôt microsoft/winget-pkgs :

        manifests\<initiale>\<Éditeur>\<Paquet>\<Version>\
            <Éditeur>.<Paquet>.yaml                  (version, DefaultLocale)
            <Éditeur>.<Paquet>.installer.yaml
            <Éditeur>.<Paquet>.locale.fr-FR.yaml     (defaultLocale)
            <Éditeur>.<Paquet>.locale.en-US.yaml     (locale)

.EXEMPLE
    .\installer\preparer-winget.ps1 -GitHubUser "hugo94z"

.NOTE
    Ce fichier doit rester enregistré en UTF-8 AVEC BOM, sinon PowerShell 5.1
    lit les accents en ANSI et les corrompt.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$GitHubUser,
    [string]$Repo = "ruche",
    [string]$Publisher = "",
    [string]$PackageName = "Ruche",
    [string]$Version = "0.1.0",
    [string]$License = "MIT",
    [string]$DefaultLocale = "fr-FR",
    [string]$InstallerPath = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Publisher) { $Publisher = $GitHubUser }
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $root "installer\Output\Ruche-Setup-$Version.exe"
}
if (-not $OutputDir) { $OutputDir = Join-Path $root "winget" }

# Écrit du texte en UTF-8 SANS BOM (les manifestes doivent rester propres).
function Write-Utf8([string]$Path, [string]$Text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

$AppId = "{8F6C2A54-3D71-4B9E-9A2F-5C1E7B4D0A33}"

if (-not (Test-Path $InstallerPath)) {
    Write-Error "Installateur introuvable : $InstallerPath`nCompilez d'abord : ISCC.exe installer\ruche.iss"
}

$installer = Get-Item $InstallerPath
Write-Host ("Installateur : {0} ({1:N1} Mo)" -f $installer.Name, ($installer.Length / 1MB))

$hash = (Get-FileHash -Path $installer.FullName -Algorithm SHA256).Hash
Write-Host "SHA-256     : $hash"

$packageId = "$Publisher.$PackageName"
$versionDir = Join-Path $OutputDir "manifests\$($Publisher.Substring(0,1).ToLower())\$Publisher\$PackageName\$Version"
New-Item -ItemType Directory -Force -Path $versionDir | Out-Null

$url = "https://github.com/$GitHubUser/$Repo/releases/download/v$Version/$($installer.Name)"
$repoUrl = "https://github.com/$GitHubUser/$Repo"
$mv = "1.10.0"
$schemaBase = "https://aka.ms/winget-manifest"

# --- Version -------------------------------------------------------------
Write-Utf8 (Join-Path $versionDir "$packageId.yaml") @"
# yaml-language-server: `$schema=$schemaBase.version.$mv.schema.json
PackageIdentifier: $packageId
PackageVersion: $Version
DefaultLocale: $DefaultLocale
ManifestType: version
ManifestVersion: $mv
"@

# --- Installer -----------------------------------------------------------
Write-Utf8 (Join-Path $versionDir "$packageId.installer.yaml") @"
# yaml-language-server: `$schema=$schemaBase.installer.$mv.schema.json
PackageIdentifier: $packageId
PackageVersion: $Version
InstallerType: inno
Scope: user
InstallModes:
  - interactive
  - silent
  - silentWithProgress
UpgradeBehavior: install
AppsAndFeaturesEntries:
  - DisplayName: $PackageName
    Publisher: $Publisher
    ProductCode: '$AppId'
Installers:
  - Architecture: x64
    InstallerUrl: $url
    InstallerSha256: $hash
ManifestType: installer
ManifestVersion: $mv
"@

# --- Locale par défaut (fr-FR) -------------------------------------------
Write-Utf8 (Join-Path $versionDir "$packageId.locale.fr-FR.yaml") @"
# yaml-language-server: `$schema=$schemaBase.defaultLocale.$mv.schema.json
PackageIdentifier: $packageId
PackageVersion: $Version
PackageLocale: fr-FR
Publisher: $Publisher
PublisherUrl: https://github.com/$GitHubUser
PublisherSupportUrl: $repoUrl/issues
PackageName: $PackageName
PackageUrl: $repoUrl
License: $License
LicenseUrl: $repoUrl/blob/main/LICENSE
ShortDescription: Messagerie pair-à-pair avec appels et partage d'écran.
Description: |-
  Ruche est une application de messagerie décentralisée : discussion texte,
  partage d'images et de vidéos, appels audio/vidéo de groupe et partage
  d'écran. Aucun serveur central n'est nécessaire : les participants se
  relient directement entre eux et le rôle d'hôte bascule automatiquement.
Moniker: ruche
Tags:
  - chat
  - messagerie
  - p2p
  - peer-to-peer
  - webrtc
  - visioconference
  - partage-ecran
ReleaseNotesUrl: $repoUrl/releases/tag/v$Version
ManifestType: defaultLocale
ManifestVersion: $mv
"@

# --- Locale secondaire (en-US) -------------------------------------------
Write-Utf8 (Join-Path $versionDir "$packageId.locale.en-US.yaml") @"
# yaml-language-server: `$schema=$schemaBase.locale.$mv.schema.json
PackageIdentifier: $packageId
PackageVersion: $Version
PackageLocale: en-US
Publisher: $Publisher
PublisherUrl: https://github.com/$GitHubUser
PublisherSupportUrl: $repoUrl/issues
PackageName: $PackageName
PackageUrl: $repoUrl
License: $License
LicenseUrl: $repoUrl/blob/main/LICENSE
ShortDescription: Peer-to-peer messaging with calls and screen sharing.
Description: |-
  Ruche is a decentralised messaging application: text chat, image and video
  sharing, group audio/video calls and screen sharing. No central server is
  required: peers connect directly to each other and the host role fails over
  automatically.
Tags:
  - chat
  - messaging
  - p2p
  - peer-to-peer
  - webrtc
  - video-call
  - screen-sharing
ReleaseNotesUrl: $repoUrl/releases/tag/v$Version
ManifestType: locale
ManifestVersion: $mv
"@

Write-Host ""
Write-Host "Manifestes ecrits dans :"
Write-Host "  $versionDir" -ForegroundColor Green
Get-ChildItem $versionDir | ForEach-Object { Write-Host "    $($_.Name)" }
Write-Host ""
Write-Host "Validation : winget validate --manifest `"$versionDir`""
Write-Host "Voir installer\WINGET.md" -ForegroundColor Cyan
