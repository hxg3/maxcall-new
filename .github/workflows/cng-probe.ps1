$ErrorActionPreference = 'Stop'

$h = Get-Content "UpdatePublicKey.h" -Raw
$b = $h.Substring($h.IndexOf('g_maxcallUpdatePublicKey[] = {'))
$blob = [byte[]]([regex]::Matches($b.Substring(0, $b.IndexOf('};')), '0x([0-9A-Fa-f]{2})') | ForEach-Object { [Convert]::ToByte($_.Groups[1].Value, 16) })
$cbExp = [BitConverter]::ToUInt32($blob, 8)
$cbMod = [BitConverter]::ToUInt32($blob, 12)
$stored = [byte[]]$blob[($cbExp + 24)..($cbExp + 23 + $cbMod)]
$reversed = [byte[]]$stored.Clone()
[Array]::Reverse($reversed)

"stored  first8: $(($stored[0..7]   | ForEach-Object { $_.ToString('x2') }) -join ' ')"
"stored  last8 : $(($stored[-8..-1] | ForEach-Object { $_.ToString('x2') }) -join ' ')"
"rev     first8: $(($reversed[0..7]   | ForEach-Object { $_.ToString('x2') }) -join ' ')"
"rev     last8 : $(($reversed[-8..-1] | ForEach-Object { $_.ToString('x2') }) -join ' ')"

$wc = New-Object System.Net.WebClient
$msg = $wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta")
$sigTxt = [System.Text.Encoding]::ASCII.GetString($wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta.sig")).Trim()
$sig = [Convert]::FromBase64String($sigTxt)

function New-PublicKeyBlob([byte[]]$modulus) {
    $pub = New-Object System.Collections.Generic.List[byte]
    $pub.AddRange([byte[]]@(0x06, 0x02, 0x00, 0x00))
    $pub.AddRange([BitConverter]::GetBytes([uint32]0x0000A400))
    $pub.AddRange([BitConverter]::GetBytes([uint32]0x31415352))
    $pub.AddRange([BitConverter]::GetBytes([uint32]3072))
    $pub.AddRange([BitConverter]::GetBytes([uint32]0x00010001))
    $pub.AddRange($modulus)
    return $pub.ToArray()
}

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

function Try-Capi([string]$label, [byte[]]$modulus) {
    $hProv = [IntPtr]::Zero
    [void][Capi]::CryptAcquireContextA([ref]$hProv, $null, $null, [uint32]24, [uint32]0xF0000000L)
    $hKey = [IntPtr]::Zero
    $okImport = [Capi]::CryptImportKey($hProv, (New-PublicKeyBlob $modulus), (24 + $modulus.Length), [IntPtr]::Zero, 0, [ref]$hKey)
    $hHash = [IntPtr]::Zero
    [void][Capi]::CryptCreateHash($hProv, 0x0000800C, [IntPtr]::Zero, 0, [ref]$hHash)
    [void][Capi]::CryptHashData($hHash, $msg, $msg.Length, 0)
    $ok = [Capi]::CryptVerifySignatureA($hHash, $sig, $sig.Length, $hKey, $null, 0)
    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    "capi $label : import=$okImport verify=$ok err=$err"
    [void][Capi]::CryptDestroyHash($hHash)
    [void][Capi]::CryptDestroyKey($hKey)
    [void][Capi]::CryptReleaseContext($hProv, 0)
}

function Try-DotNet([string]$label, [byte[]]$modulusBE) {
    $rsa = New-Object System.Security.Cryptography.RSACng
    $p = New-Object System.Security.Cryptography.RSAParameters
    $p.Modulus = $modulusBE
    $p.Exponent = [byte[]]@(0x01, 0x00, 0x01)
    $rsa.ImportParameters($p)
    "dotnet $label : $($rsa.VerifyData($msg, $sig, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1))"
}

Try-Capi "modulus-as-stored" $stored
Try-Capi "modulus-reversed"  $reversed
Try-DotNet "modulus-as-stored" $stored
Try-DotNet "modulus-reversed"  $reversed
