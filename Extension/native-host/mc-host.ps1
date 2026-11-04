# Native messaging host: MaxCare Reception Assistant self-updater.
# Called by the extension (one click) - downloads the new build, replaces the
# extension folder, then the extension reloads itself.
$ErrorActionPreference = "Stop"
$BaseDir = Join-Path $env:LOCALAPPDATA "MaxCare"
$ExtDir  = Join-Path $BaseDir "Extension"
$ZipUrl  = "http://maxcall.local:3001/ext/extension.zip"

function Read-Message {
    $stdin = [Console]::OpenStandardInput()
    $lenBuf = New-Object byte[] 4
    if ($stdin.Read($lenBuf, 0, 4) -ne 4) { exit 0 }
    $len = [BitConverter]::ToInt32($lenBuf, 0)
    if ($len -le 0 -or $len -gt 1MB) { exit 0 }
    $buf = New-Object byte[] $len
    $off = 0
    while ($off -lt $len) {
        $n = $stdin.Read($buf, $off, $len - $off)
        if ($n -le 0) { exit 0 }
        $off += $n
    }
    [Text.Encoding]::UTF8.GetString($buf) | ConvertFrom-Json
}

function Send-Message($obj) {
    $json = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress))
    $out = [Console]::OpenStandardOutput()
    $out.Write([BitConverter]::GetBytes([int]$json.Length), 0, 4)
    $out.Write($json, 0, $json.Length)
    $out.Flush()
}

try {
    $msg = Read-Message
    if ($msg.cmd -ne "update") {
        Send-Message @{ ok = $false; error = "unknown_cmd" }
        exit 0
    }
    $zip = Join-Path $env:TEMP "mcext.zip"
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
    New-Item -ItemType Directory -Path $ExtDir -Force | Out-Null
    # Clean old files (locked file => close Chrome and retry)
    Get-ChildItem $ExtDir -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $ExtDir -Force
    $ver = ((Get-Content (Join-Path $ExtDir "manifest.json") -Raw) | ConvertFrom-Json).version
    Send-Message @{ ok = $true; version = $ver }
} catch {
    Send-Message @{ ok = $false; error = $_.Exception.Message }
}
