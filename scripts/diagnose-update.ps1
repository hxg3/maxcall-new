# diagnose-update.ps1
#
# Why this exists
# ---------------
# MaxCall reported WinInet error 12152 (ERROR_INTERNET_SECURE_FAILURE) while
# fetching its update manifest. That failure happens before any application data
# is exchanged, so the cause is always on the machine: a wrong clock, a TLS
# version the system cannot negotiate, an intercepting proxy, or DNS.
# Guessing from the number wastes a build cycle every time, so this collects the
# evidence in one run.
#
# How to use
# ----------
#   1. Copy this file to the Windows machine that cannot update.
#   2. Open PowerShell and run:
#          powershell -NoProfile -ExecutionPolicy Bypass -File .\diagnose-update.ps1
#   3. Send back the file it writes: maxcall-update-diagnosis.txt
#
# Why this deliberately uses Windows PowerShell 5.1
# -------------------------------------------------
# MaxCall is a WinInet client. PowerShell 7 (.NET Core) uses HttpClient, which
# has its own TLS stack and would pass even when WinInet fails. Windows
# PowerShell 5.1 runs on .NET Framework, whose HttpWebRequest goes through
# WinInet - the same stack as the application. That is the only honest test.

$ErrorActionPreference = 'Continue'
$Report = Join-Path (Get-Location) 'maxcall-update-diagnosis.txt'
$ManifestUrl  = 'https://github.com/hxg3/maxcall-new/releases/download/manifest-stable/update.json'
$SignatureUrl = 'https://github.com/hxg3/maxcall-new/releases/download/manifest-stable/update.json.sig'

function Write-Section($title) {
    Add-Content $Report ''
    Add-Content $Report ('-' * 74)
    Add-Content $Report $title
    Add-Content $Report ('-' * 74)
}
function Write-Line($text) { Add-Content $Report $text; Write-Host $text }

Set-Content $Report '========================================================================'
Add-Content $Report ' MaxCall update-channel diagnosis'
Add-Content $Report (" Generated: " + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss K'))
Add-Content $Report '========================================================================'

Write-Line "Writing report to $Report"

# --- 1. Clock --------------------------------------------------------------
# A machine whose date is wrong treats every certificate as not yet valid or
# already expired. This is the single most common cause of 12152.
Write-Section '1. System clock (a wrong date invalidates every certificate)'
Write-Line ("  Local time   : " + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
Write-Line ("  UTC          : " + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'))
Write-Line ("  Time zone    : " + (Get-TimeZone).Id)
try {
    Write-Line ("  w32tm status : " + (w32tm /query /status 2>&1 | Select-Object -First 3 | Out-String).Trim())
} catch { Write-Line '  w32tm        : not available' }

# --- 2. DNS ----------------------------------------------------------------
Write-Section '2. Name resolution'
foreach ($host_ in @('github.com', 'release-assets.githubusercontent.com')) {
    try {
        $addrs = [System.Net.Dns]::GetHostAddresses($host_) | ForEach-Object { $_.IPAddressToString }
        Write-Line ("  $host_ -> " + ($addrs -join ', '))
    } catch {
        Write-Line ("  $host_ -> FAILED: " + $_.Exception.Message)
    }
}

# --- 3. TLS ----------------------------------------------------------------
Write-Section '3. TLS'
Write-Line ("  PowerShell edition : " + $PSVersionTable.PSEdition + ' ' + $PSVersionTable.PSVersion)
try {
    Write-Line ("  .NET outbound default : " + [Net.ServicePointManager]::SecurityProtocol)
} catch { Write-Line '  .NET outbound default : could not read' }

foreach ($ver in @('TLS 1.0', 'TLS 1.1', 'TLS 1.2')) {
    $key = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$ver\Client"
    if (Test-Path $key) {
        $val = (Get-ItemProperty -Path $key -Name Enabled -ErrorAction SilentlyContinue).Enabled
        Write-Line ("  Schannel $ver : " + $(if ($null -ne $val) { if ($val -eq 0) { 'disabled' } else { 'enabled' } } else { 'not overridden' }))
    } else {
        Write-Line ("  Schannel $ver : not overridden")
    }
}

# --- 4. Proxies ------------------------------------------------------------
# A proxy that requires credentials makes WinInet fail outright, and a
# TLS-inspecting proxy produces exactly the symptom being chased here.
Write-Section '4. Proxy configuration'
Write-Line '  WinHTTP (used by services and some SDKs):'
Write-Line ((netsh winhttp show proxy 2>&1 | Out-String).TrimEnd())

$inetKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
Write-Line '  WinINET (used by MaxCall):'
if (Test-Path $inetKey) {
    $props = Get-ItemProperty $inetKey
    Write-Line ("    ProxyEnable   : " + $props.ProxyEnable)
    Write-Line ("    ProxyServer   : " + $props.ProxyServer)
    Write-Line ("    AutoConfigURL : " + $props.AutoConfigURL)
} else {
    Write-Line '    (no Internet Settings key)'
}

# --- 5. The exact request MaxCall makes ------------------------------------
Write-Section '5. The exact request MaxCall makes (WinInet, like the app)'
foreach ($url in @($ManifestUrl, $SignatureUrl)) {
    Write-Line "  GET $url"
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.Method = 'GET'
        $req.AllowAutoRedirect = $true
        $req.Timeout = 30000
        $req.ReadWriteTimeout = 30000
        $req.UserAgent = 'MaxCall/1.0 (Windows NT)'

        $resp = $req.GetResponse()
        $status = [int]$resp.StatusCode
        $final = $resp.ResponseUri
        $len = $resp.ContentLength
        Write-Line ("    HTTP $status   content-length=$len")
        Write-Line ("    final URL : $final")
        $resp.Close()
        if ($status -ne 200) { Write-Line '    !! not 200' }
    } catch [System.Net.WebException] {
        $ex = $_.Exception
        Write-Line ("    FAILED: " + $ex.Message)
        Write-Line ("    Win32 error code : " + $ex.ErrorCode)
        if ($ex.Status) { Write-Line ("    WebExceptionStatus: " + $ex.Status) }
        if ($ex.Response) {
            try { Write-Line ("    server said     : HTTP " + [int]$ex.Response.StatusCode) } catch {}
        }
        Write-Line '    Compare this code with the one in maxcall_debug.log.'
    } catch {
        Write-Line ("    FAILED: " + $_.Exception.Message)
    }
    Write-Line ''
}

# --- 6. Plain TCP reachability --------------------------------------------
# Separates "cannot reach the network at all" from "cannot do TLS".
Write-Section '6. TCP reachability (port 443)'
foreach ($target in @(@('github.com', 443), @('release-assets.githubusercontent.com', 443))) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($target[0], $target[1], $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(10000)) {
            $client.EndConnect($iar)
            Write-Line ("  {0}:{1} connected" -f $target[0], $target[1])
        } else {
            Write-Line ("  {0}:{1} TIMED OUT" -f $target[0], $target[1])
        }
    } catch {
        Write-Line ("  {0}:{1} FAILED - {2}" -f $target[0], $target[1], $_.Exception.Message)
    } finally {
        $client.Close()
    }
}

# --- 7. What the app recorded ---------------------------------------------
Write-Section '7. What MaxCall itself recorded'
$exeDir = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($candidate in @($exeDir, (Get-Location).Path, "$env:APPDATA\MaxCall")) {
    $log = Join-Path $candidate 'maxcall_debug.log'
    if (Test-Path $log) {
        Write-Line "  Found $log"
        Get-Content $log -Tail 40 | ForEach-Object { Write-Line ('    ' + $_) }
        break
    }
}
if (-not (Get-ChildItem -Path $exeDir -Filter 'maxcall_debug.log' -ErrorAction SilentlyContinue)) {
    Write-Line '  (no maxcall_debug.log found next to this script)'
}

Add-Content $Report ''
Add-Content $Report '========================================================================'
Add-Content $Report ' End of report'
Add-Content $Report '========================================================================'

Write-Host ''
Write-Host "Done. Send this file:" -ForegroundColor Green
Write-Host "  $Report" -ForegroundColor Green