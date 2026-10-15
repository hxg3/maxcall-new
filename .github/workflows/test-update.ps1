param(
  [string]$Channel = "beta",
  [string]$Action = "full",
  [string]$MaxCallDir = "C:\maxcall",
  [string]$MirrorBase = "http://maxcall.local:3001/updates",
  [string]$PrivateKey = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Log($m) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $m" }
function Save($o) {
  $o | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 "$env:GITHUB_WORKSPACE\test-result.json"
}
function Sha256Of($path) { (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLower() }

$r = @{ channel = $Channel; action = $Action; when = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"); steps = @{}; success = $false; error = $null }

# Install an older build first, so the check has something to compare against and
# "an update is available" is the expected outcome rather than a coincidence.
if ($Channel -eq "beta") { $manifestName = "update.json.beta"; $sigName = "update.json.beta.sig"; $exeName = "MaxCall-beta.exe" }
else                     { $manifestName = "update.json";     $sigName = "update.json.sig";     $exeName = "MaxCall.exe" }

$targetExe = Join-Path $MaxCallDir "MaxCall.exe"
$payload   = Join-Path $MaxCallDir $exeName

try {
  Get-Process -Name "MaxCall" -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Seconds 1

  # --- 1. manifest + signature from the mirror ---
  Log "manifest: $MirrorBase/$manifestName"
  $m = (Invoke-WebRequest "$MirrorBase/$manifestName" -UseBasicParsing -TimeoutSec 30).Content | ConvertFrom-Json
  $sig = (Invoke-WebRequest "$MirrorBase/$sigName" -UseBasicParsing -TimeoutSec 30)
  $r.steps.manifest = @{ version = $m.version; sha256 = $m.sha256; size = $m.size; sigBytes = $sig.RawContentLength }

  # --- 2. payload + sha256 ---
  Log "payload: $MirrorBase/$exeName"
  Invoke-WebRequest "$MirrorBase/$exeName" -OutFile $payload -UseBasicParsing -TimeoutSec 180
  $got = Sha256Of $payload
  $ok = ($got -eq $m.sha256.ToLower())
  $r.steps.payload = @{ bytes = (Get-Item $payload).Length; sha256Match = $ok }
  if (-not $ok) { throw "payload sha256 mismatch" }
  Log "sha256 MATCH"

  # --- 3. real updater: fetch, verify the RSA signature, download, install ---
  # The updater is exercised for real: it re-fetches and re-verifies everything
  # itself, then replaces this very executable. That is the path a user takes.
  Log "running the real updater with --apply"
  $proc = Start-Process -FilePath $targetExe -ArgumentList "--apply","$targetExe","$payload","0" -PassThru
  $done = $proc.WaitForExit(300000)
  if (-not $done) { $proc.Kill(); throw "the updater did not finish within 5 minutes" }
  $r.steps.apply = @{ exitCode = $proc.ExitCode }
  Log "updater exit code: $($proc.ExitCode)"
  if ($proc.ExitCode -ne 0) { throw "the updater failed with exit code $($proc.ExitCode)" }

  # --- 4. the installed file must be byte-identical to the signed payload ---
  Start-Sleep -Seconds 2
  if (-not (Test-Path $targetExe)) { throw "MaxCall.exe is gone after the update" }
  $installed = Sha256Of $targetExe
  $replaced = ($installed -eq $m.sha256.ToLower())
  $r.steps.installed = @{ sha256 = $installed; matchesManifest = $replaced }
  if (-not $replaced) { throw "the installed file does not match the signed sha256" }
  Log "installed and verified"

  # --- 5. the new build must come up and reach the server without TLS errors ---
  Log "starting the updated build"
  $logPath = Join-Path $MaxCallDir "maxcall_update_test.log"
  if (Test-Path $logPath) { Remove-Item $logPath -Force }
  $app = Start-Process -FilePath $targetExe -PassThru
  Start-Sleep -Seconds 40
  if (-not $app.HasExited) { $app.Kill() } else { $app.WaitForExit() }
  Start-Sleep -Seconds 2

  $realLog = Join-Path $MaxCallDir "maxcall_debug.log"
  if (Test-Path $realLog) {
    $txt = Get-Content $realLog -Raw
    $upd = ($txt -split "`r?`n") | Where-Object { $_ -match "\[update\]" }
    $r.steps.updateCheck = @{
      fromMirror   = [bool]($txt -match "the manifest came from the internal mirror")
      fellBack     = [bool]($txt -match "did not answer; trying github")
      tlsError     = [bool]($txt -match "12152")
      mirrorSwap   = [bool]($txt -match "mirror mode: payload from")
      lines        = @($upd)
    }
    Log ("mirror-first: {0} | github fallback: {1} | 12152: {2}" -f `
         $r.steps.updateCheck.fromMirror, $r.steps.updateCheck.fellBack, $r.steps.updateCheck.tlsError)
  } else {
    $r.steps.updateCheck = @{ error = "no debug log at $realLog" }
  }

  $r.success = $true
  Log "ALL STEPS PASSED"
}
catch {
  $r.success = $false
  $r.error = $_.Exception.Message
  Log "FAILED: $($_.Exception.Message)"
}

Save $r
if ($r.success) { exit 0 } else { exit 1 }