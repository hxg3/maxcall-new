# MaxCare Reception Assistant - self-updater setup. Run ONCE per PC.
# No admin rights needed (HKCU + %LOCALAPPDATA% only).
# After this: every future update = one click inside the extension popup.
param(
    # Optional: pass the extension ID from chrome://extensions if auto-detect mismatches
    [string]$ExtensionID = ""
)
$ErrorActionPreference = "Stop"
$BaseDir = Join-Path $env:LOCALAPPDATA "MaxCare"
$ExtDir  = Join-Path $BaseDir "Extension"
$Base    = "http://maxcall.local:3001/ext"

# 1) Latest extension build
New-Item -ItemType Directory -Path $ExtDir -Force | Out-Null
$zip = Join-Path $env:TEMP "mcext.zip"
Invoke-WebRequest -Uri "$Base/extension.zip" -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $ExtDir -Force

# 2) Native messaging host files
foreach ($f in @("mc-host.ps1", "mc-host.cmd")) {
    Invoke-WebRequest -Uri "$Base/$f" -OutFile (Join-Path $BaseDir $f) -UseBasicParsing
}

# 3) Unpacked extension ID is derived from its path - or pass it via -ExtensionID
if (-not $ExtensionID) {
    $sha  = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash([Text.Encoding]::Unicode.GetBytes($ExtDir))
    $ExtensionID = -join ($hash[0..15] | ForEach-Object {
        [char](97 + ($_ -shr 4)); [char](97 + ($_ -band 15))
    })
}

$manifest = @{
    name            = "com.maxcare.updater"
    description     = "MaxCare extension self-updater"
    path            = (Join-Path $BaseDir "mc-host.cmd")
    type            = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionID/")
}
$manifestPath = Join-Path $BaseDir "com.maxcare.updater.json"
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8

$regKey = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.maxcare.updater"
New-Item -Path $regKey -Force | Out-Null
Set-ItemProperty -Path $regKey -Name "(default)" -Value $manifestPath

Write-Host ""
Write-Host "Setup done."
Write-Host "One last step (once):"
Write-Host "  chrome://extensions -> Developer mode -> Load unpacked"
Write-Host "  Choose folder: $ExtDir"
Write-Host ""
Write-Host "Registered extension ID: $ExtensionID"
Write-Host "(If chrome://extensions shows a different ID, re-run:"
Write-Host " setup-updater.ps1 -ExtensionID <that-ID> )"
