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
# The beta build runs as "MaxCall-beta", so matching the plain name left the
# previous run alive holding the debug log and the executable.
function Stop-MaxCall {
  Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -like "MaxCall*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
}
function Save($o) {
  $o | ConvertTo-Json -Depth 6 | Out-File -Encoding utf8 "$env:GITHUB_WORKSPACE\test-result.json"
}
function Sha256Of($path) { (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLower() }

$r = @{ channel = $Channel; action = $Action; when = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"); steps = @{}; success = $false; error = $null }

# Install an older build first, so the check has something to compare against and
# "an update is available" is the expected outcome rather than a coincidence.
if ($Channel -eq "beta") { $manifestName = "update.json.beta"; $sigName = "update.json.beta.sig"; $exeName = "MaxCall-beta.exe" }
else                     { $manifestName = "update.json";     $sigName = "update.json.sig";     $exeName = "MaxCall.exe" }

# The updater replaces the executable it was launched from, and reads its INI
# from the same folder. The beta build ships as MaxCall-beta.exe, so the test
# has to drive that file rather than assume MaxCall.exe.
$targetExe = Join-Path $MaxCallDir $exeName
$payload   = Join-Path $MaxCallDir $exeName

try {
  Stop-MaxCall

  # --- 1. manifest + signature from the mirror ---
  Log "manifest: $MirrorBase/$manifestName"
  $m = (Invoke-WebRequest "$MirrorBase/$manifestName" -UseBasicParsing -TimeoutSec 30).Content | ConvertFrom-Json
  $sig = (Invoke-WebRequest "$MirrorBase/$sigName" -UseBasicParsing -TimeoutSec 30)
  $sigText = if ($sig.Content -is [byte[]]) { [System.Text.Encoding]::ASCII.GetString($sig.Content) } else { [string]$sig.Content }
  $sigText = ($sigText -replace '\s', '').Trim([char]0xFEFF)
  $r.steps.manifest = @{ version = $m.version; sha256 = $m.sha256; size = $m.size; sigBytes = ([Convert]::FromBase64String($sigText)).Length }

  # --- 2. payload + sha256 ---
  Log "payload: $MirrorBase/$exeName"
  $payload = Join-Path $MaxCallDir ("payload-" + $exeName)
  Invoke-WebRequest "$MirrorBase/$exeName" -OutFile $payload -UseBasicParsing -TimeoutSec 180
  $got = Sha256Of $payload
  $ok = ($got -eq $m.sha256.ToLower())
  $r.steps.payload = @{ bytes = (Get-Item $payload).Length; sha256Match = $ok }
  $r.steps.installedVersion = $m.version
  if (-not $ok) { throw "payload sha256 mismatch" }
  Log "sha256 MATCH"

  # The machine may only have the other channel installed. Put this channel's
  # signed build at the target path, and give it an INI modelled on whatever
  # sibling already works, so the run below exercises the channel under test
  # instead of failing because a file the install never had is missing.
  if (-not (Test-Path $targetExe)) {
    Copy-Item $payload $targetExe -Force
    Log "bootstrapped $([System.IO.Path]::GetFileName($targetExe)) from the signed payload"
  }
  $iniPathEarly = [System.IO.Path]::ChangeExtension($targetExe, ".ini")
  if (-not (Test-Path $iniPathEarly)) {
    $template = Get-ChildItem $MaxCallDir -Filter "*.ini" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($template) {
      Copy-Item $template.FullName $iniPathEarly -Force
      Log "started $([System.IO.Path]::GetFileName($iniPathEarly)) from $($template.Name)"
    }
  }

  # --- 3. real updater: fetch, verify the RSA signature, download, install ---
  # The updater is exercised for real: it re-fetches and re-verifies everything
  # itself, then replaces this very executable. That is the path a user takes.
  Log "running the real updater: $targetExe --apply <target> <source> 0"
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

  # The real log has to go first. Reading it without deleting it reports whatever
  # an earlier run left behind, which is how a stale log gets mistaken for a
  # current result.
  Stop-MaxCall
  $logPath = Join-Path $MaxCallDir "maxcall_debug.log"
  for ($i = 0; $i -lt 5 -and (Test-Path $logPath); $i++) {
    try { Remove-Item $logPath -Force -ErrorAction Stop } catch { Start-Sleep -Seconds 2 }
  }
  if (Test-Path $logPath) { throw "the debug log is still locked: $logPath" }

  # Point the installed copy at the channel under test, otherwise it checks the
  # stable manifest and the beta result is never exercised.
  $iniPath = [System.IO.Path]::ChangeExtension($targetExe, ".ini")
  if (Test-Path $iniPath) {
    $ini = Get-Content $iniPath -Raw
    $channelValue = if ($Channel -eq 'beta') { 1 } else { 0 }
    if ($ini -match '(?m)^updateChannel=') {
      $ini = [regex]::Replace($ini, '(?m)^updateChannel=.*$', "updateChannel=$channelValue")
    } else {
      $ini = $ini.TrimEnd() + "`r`nupdateChannel=$channelValue`r`n"
    }
    if ($ini -match '(?m)^enableLog=') {
      $ini = [regex]::Replace($ini, '(?m)^enableLog=.*$', "enableLog=1")
    } else {
      $ini = $ini.TrimEnd() + "`r`nenableLog=1`r`n"
    }
    Set-Content -Path $iniPath -Value $ini -Encoding UTF8
    Log "set updateChannel in $([System.IO.Path]::GetFileName($iniPath))"
  } else {
    Log "no INI beside the executable: $iniPath"
  }

  # The app opens its log with a relative path, so it lands in whatever the
  # working directory is. Without this the log is written under the runner's
  # workspace and the test looks in the wrong place.
  $app = Start-Process -FilePath $targetExe -WorkingDirectory $MaxCallDir -PassThru
  Log "launched, waiting 40s"
  Start-Sleep -Seconds 40
  if (-not $app.HasExited) { $app.Kill() } else { $app.WaitForExit() }
  Start-Sleep -Seconds 2

  $realLog = Join-Path $MaxCallDir "maxcall_debug.log"
  if (-not (Test-Path $realLog)) {
    $found = Get-ChildItem $env:GITHUB_WORKSPACE -Filter "maxcall_debug.log" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { Log "log landed in the workspace: $($found.FullName)" }
  }
  if (Test-Path $realLog) {
    $txt = Get-Content $realLog -Raw
    $upd = @(($txt -split "`r?`n") | Where-Object { $_ -match "\[update\]" } | ForEach-Object { "$_" })
    $r.steps.updateCheck = @{
      fromMirror   = [bool]($txt -match "the manifest came from the internal mirror")
      fellBack     = [bool]($txt -match "did not answer; trying github")
      tlsError     = [bool]($txt -match "12152")
      mirrorSwap   = [bool]($txt -match "mirror mode: payload from")
      oldCodeAbsent = -not ($txt -match "github.com unreachable, falling back")
      # The manifest and its signature being served is not the same as the
      # signature being accepted. The acceptance is asserted on its own line so a
      # rejection cannot be read as a pass while the payload still installs.
      sigRejected   = [bool]($txt -match "signature rejected")
      sigAccepted   = [bool]($txt -match "manifest signature verified")
      lines        = @($upd)
      logSize      = (Get-Item $realLog).Length
      logHead      = @((Get-Content $realLog -TotalCount 25) | ForEach-Object { "$_" })
    }
    Log ("mirror-first: {0} | github fallback: {1} | 12152: {2}" -f `
         $r.steps.updateCheck.fromMirror, $r.steps.updateCheck.fellBack, $r.steps.updateCheck.tlsError)
  } else {
    $r.steps.updateCheck = @{ error = "no debug log at $realLog" }
  }

  if ($r.steps.updateCheck.sigRejected) {
    throw "the client rejected the manifest signature"
  }
  if (-not $r.steps.updateCheck.sigAccepted) {
    throw "the client never reported the manifest signature as verified"
  }

  # --- 6. the install path itself, driven without a GUI ---
  # The banner's button did nothing for every user because a guard inside the
  # install path accepted https only, while the mirror - the only route this
  # network has - serves plain http. --self-update runs that same guard and the
  # download behind it, so a regression fails here instead of turning the button
  # into a silent no-op again.
  Log "driving the install path headlessly (--self-update)"
  Stop-MaxCall
  for ($i = 0; $i -lt 5 -and (Test-Path $realLog); $i++) {
    try { Remove-Item $realLog -Force -ErrorAction Stop } catch { Start-Sleep -Seconds 2 }
  }

  $self = Start-Process -FilePath $targetExe -ArgumentList "--self-update" -WorkingDirectory $MaxCallDir -PassThru
  $selfDone = $self.WaitForExit(300000)
  if (-not $selfDone) { $self.Kill(); throw "--self-update did not finish within 5 minutes" }

  $selfLog = if (Test-Path $realLog) { Get-Content $realLog -Raw } else { "" }
  $r.steps.selfUpdate = @{
    exitCode    = $self.ExitCode
    rejected    = [bool]($selfLog -match "self-update: manifest rejected")
    upToDate    = [bool]($selfLog -match "self-update: nothing newer")
    applied     = [bool]($selfLog -match "self-update: apply stage started")
    manifestErr = [bool]($selfLog -match "self-update: manifest failed")
    downloadErr = [bool]($selfLog -match "self-update: download failed")
    checksumErr = [bool]($selfLog -match "self-update: payload does not match")
  }
  Log ("self-update: exit {0} | rejected {1} | up-to-date {2} | applied {3}" -f `
       $r.steps.selfUpdate.exitCode, $r.steps.selfUpdate.rejected, `
       $r.steps.selfUpdate.upToDate, $r.steps.selfUpdate.applied)

  if ($r.steps.selfUpdate.rejected) {
    throw "the install path rejected the mirror's payload URL"
  }
  if ($r.steps.selfUpdate.exitCode -eq 0) {
    # A newer build really was installed: the swap must leave the signed payload.
    if (-not $r.steps.selfUpdate.applied) {
      throw "--self-update downloaded a build but never handed over to the apply stage"
    }
    Start-Sleep -Seconds 3
    if ((Sha256Of $targetExe) -ne (Sha256Of $payload)) {
      throw "the install path replaced the executable with something other than the signed payload"
    }
    Log "the install path swapped in the signed payload"
  } elseif ($r.steps.selfUpdate.exitCode -eq 2) {
    # Nothing newer exists: the guard passed and the comparison ran, which is
    # exactly the step that used to abort before either happened.
    if (-not $r.steps.selfUpdate.upToDate) {
      throw "--self-update reported nothing newer without logging why"
    }
  } else {
    throw "the install path failed: exit $($r.steps.selfUpdate.exitCode)"
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