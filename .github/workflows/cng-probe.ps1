$ErrorActionPreference = 'Stop'

$h = Get-Content "UpdatePublicKey.h" -Raw
$b = $h.Substring($h.IndexOf('g_maxcallUpdatePublicKey[] = {'))
$blob = [byte[]]([regex]::Matches($b.Substring(0, $b.IndexOf('};')), '0x([0-9A-Fa-f]{2})') | ForEach-Object { [Convert]::ToByte($_.Groups[1].Value, 16) })
$cbExp = [BitConverter]::ToUInt32($blob, 8)
$cbMod = [BitConverter]::ToUInt32($blob, 12)
$modulus = [byte[]]$blob[($cbExp + 24)..($cbExp + 23 + $cbMod)]
"blob bytes      : $($blob.Length)  (magic 0x{0:X8}, exp {1} bytes, mod {2} bytes)" -f [BitConverter]::ToUInt32($blob, 0), $cbExp, $cbMod

$wc = New-Object System.Net.WebClient
$msg = $wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta")
$sigTxt = [System.Text.Encoding]::ASCII.GetString($wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta.sig")).Trim()
$sig = [Convert]::FromBase64String($sigTxt)
"manifest bytes  : $($msg.Length), signature bytes: $($sig.Length)"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Capi {
    [DllImport("advapi32.dll", CharSet=CharSet.Ansi, SetLastError=true)]
    public static extern bool CryptAcquireContextA(out IntPtr hProv, string pszContainer, string pszProvider, uint dwProvType, uint dwFlags);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptImportKey(IntPtr hProv, byte[] pbData, uint dwDataLen, IntPtr hPubKey, uint dwFlags, out IntPtr phKey);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptCreateHash(IntPtr hProv, uint Algid, IntPtr hKey, uint dwFlags, out IntPtr phHash);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptHashData(IntPtr hHash, byte[] pbData, uint dwDataLen, uint dwFlags);
    [DllImport("advapi32.dll", CharSet=CharSet.Ansi, SetLastError=true)]
    public static extern bool CryptVerifySignatureA(IntPtr hHash, byte[] pbSignature, uint dwSigLen, IntPtr hPubKey, string sDescription, uint dwFlags);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptDestroyHash(IntPtr hHash);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptDestroyKey(IntPtr hKey);
    [DllImport("advapi32.dll", SetLastError=true)]
    public static extern bool CryptReleaseContext(IntPtr hProv, uint dwFlags);
}
"@

$pub = New-Object System.Collections.Generic.List[byte]
$pub.AddRange([byte[]]@(0x06, 0x02, 0x00, 0x00))          # PUBLICKEYBLOB, version 2, reserved
$pub.AddRange([BitConverter]::GetBytes([uint32]0x0000A400)) # CALG_RSA_KEYX
$pub.AddRange([BitConverter]::GetBytes([uint32]0x31415352)) # RSA1 magic
$pub.AddRange([BitConverter]::GetBytes([uint32]3072))       # bit length
$pub.AddRange([BitConverter]::GetBytes([uint32]0x00010001)) # public exponent
$pub.AddRange($modulus)
$pubBlob = $pub.ToArray()
"publickeyblob   : $($pubBlob.Length) bytes"

$hProv = [IntPtr]::Zero
$ok = [Capi]::CryptAcquireContextA([ref]$hProv, $null, $null, 24, 0xF0000000)
"acquire context : $ok  (err $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
$hKey = [IntPtr]::Zero
$ok = [Capi]::CryptImportKey($hProv, $pubBlob, $pubBlob.Length, [IntPtr]::Zero, 0, [ref]$hKey)
"import key      : $ok  (err $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
$hHash = [IntPtr]::Zero
$ok = [Capi]::CryptCreateHash($hProv, 0x0000800C, [IntPtr]::Zero, 0, [ref]$hHash)
"create hash     : $ok  (err $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
$ok = [Capi]::CryptHashData($hHash, $msg, $msg.Length, 0)
"hash data       : $ok  (err $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
$ok = [Capi]::CryptVerifySignatureA($hHash, $sig, $sig.Length, $hKey, $null, 0)
"verify signature: $ok  (err $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))  <- this is the answer"

[void][Capi]::CryptDestroyHash($hHash)
[void][Capi]::CryptDestroyKey($hKey)
[void][Capi]::CryptReleaseContext($hProv, 0)
