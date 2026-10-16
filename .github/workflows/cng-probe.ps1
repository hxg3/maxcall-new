$ErrorActionPreference = 'Stop'

$h = Get-Content "UpdatePublicKey.h" -Raw
$b = $h.Substring($h.IndexOf('g_maxcallUpdatePublicKey[] = {'))
$blob = [byte[]]([regex]::Matches($b.Substring(0, $b.IndexOf('};')), '0x([0-9A-Fa-f]{2})') | ForEach-Object { [Convert]::ToByte($_.Groups[1].Value, 16) })
$cbExp = [BitConverter]::ToUInt32($blob, 8)
$cbMod = [BitConverter]::ToUInt32($blob, 12)
$modLE = [byte[]]$blob[($cbExp + 24)..($cbExp + 23 + $cbMod)]
$expLE = [byte[]]$blob[24..($cbExp + 23)]

$wc = New-Object System.Net.WebClient
$msg = $wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta")
$sigTxt = [System.Text.Encoding]::ASCII.GetString($wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta.sig")).Trim()
$sig = [Convert]::FromBase64String($sigTxt)
$digest = [System.Security.Cryptography.SHA256]::Create().ComputeHash($msg)

function Rebuild-BcryptBlob {
    $o = New-Object System.Collections.Generic.List[byte]
    $o.AddRange([BitConverter]::GetBytes([uint32]0x31415352))
    $o.AddRange([BitConverter]::GetBytes([uint32]3072))
    $o.AddRange([BitConverter]::GetBytes([uint32]3))
    $o.AddRange([BitConverter]::GetBytes([uint32]384))
    $o.AddRange([BitConverter]::GetBytes([uint32]0))
    $o.AddRange([BitConverter]::GetBytes([uint32]0))
    $o.AddRange([byte[]]@(0x01, 0x00, 0x01))
    $o.AddRange($modLE)
    return $o.ToArray()
}

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class CN {
    [DllImport("bcrypt.dll", CharSet=CharSet.Unicode)]
    public static extern int BCryptOpenAlgorithmProvider(out IntPtr phAlgorithm, string pszAlgId, string pszImplementation, int dwFlags);
    [DllImport("bcrypt.dll", CharSet=CharSet.Unicode)]
    public static extern int BCryptImportKeyPair(IntPtr hAlgorithm, IntPtr hImportKey, string pszBlobType, out IntPtr phKey, byte[] pbInput, int cbInput, int dwFlags);
    [DllImport("bcrypt.dll", CharSet=CharSet.Unicode)]
    public static extern int BCryptVerifySignature(IntPtr hKey, IntPtr pPaddingInfo, byte[] pbHashValue, int cbHashValue, byte[] pbSignature, int cbSignature, int dwFlags);
}
[StructLayout(LayoutKind.Sequential)]
public struct Pkcs1Info { public IntPtr pszAlgId; }
public static class Capi2 {
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

$algPtr = [Runtime.InteropServices.Marshal]::StringToHGlobalUni("SHA256")
$info = New-Object Pkcs1Info
$info.pszAlgId = $algPtr
$infoPtr = [Runtime.InteropServices.Marshal]::AllocHGlobal([Runtime.InteropServices.Marshal]::SizeOf($info))
[Runtime.InteropServices.Marshal]::StructureToPtr($info, $infoPtr, $false)

function Try-Cng([string]$label, [byte[]]$keyBlob, [int]$flags) {
    $hAlg = [IntPtr]::Zero
    [void][CN]::BCryptOpenAlgorithmProvider([ref]$hAlg, "RSA", $null, 0)
    $hKey = [IntPtr]::Zero
    $imp = [CN]::BCryptImportKeyPair($hAlg, [IntPtr]::Zero, "RSAPUBLIC_BLOB", [ref]$hKey, $keyBlob, $keyBlob.Length, 0)
    $p = if ($flags -eq 2) { $infoPtr } else { [IntPtr]::Zero }
    $v = [CN]::BCryptVerifySignature($hKey, $p, $digest, $digest.Length, $sig, $sig.Length, $flags)
    "cng $label : import=0x$('{0:X8}' -f $imp) verify=0x$('{0:X8}' -f $v)"
}

function Try-Capi2([string]$label, [uint32]$algId, [uint32]$provType) {
    $hProv = [IntPtr]::Zero
    $acq = [Capi2]::CryptAcquireContextA([ref]$hProv, $null, $null, $provType, [uint32]0xF0000000L)
    $pb = New-Object System.Collections.Generic.List[byte]
    $pb.AddRange([byte[]]@(0x06, 0x02, 0x00, 0x00))
    $pb.AddRange([BitConverter]::GetBytes($algId))
    $pb.AddRange([BitConverter]::GetBytes([uint32]0x31415352))
    $pb.AddRange([BitConverter]::GetBytes([uint32]3072))
    $pb.AddRange([BitConverter]::GetBytes([uint32]0x00010001))
    $pb.AddRange($modLE)
    $hKey = [IntPtr]::Zero
    $imp = [Capi2]::CryptImportKey($hProv, $pb.ToArray(), $pb.Count, [IntPtr]::Zero, 0, [ref]$hKey)
    $hHash = [IntPtr]::Zero
    $ch = [Capi2]::CryptCreateHash($hProv, 0x0000800C, [IntPtr]::Zero, 0, [ref]$hHash)
    [void][Capi2]::CryptHashData($hHash, $msg, $msg.Length, 0)
    $v = [Capi2]::CryptVerifySignatureA($hHash, $sig, $sig.Length, $hKey, $null, 0)
    $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    "capi $label : acq=$acq import=$imp hash=$ch verify=$v err=$err"
    [void][Capi2]::CryptDestroyHash($hHash)
    [void][Capi2]::CryptDestroyKey($hKey)
    [void][Capi2]::CryptReleaseContext($hProv, 0)
}

Try-Cng "header-blob  flags=2+info" $blob 2
Try-Cng "rebuilt-blob flags=2+info" (Rebuild-BcryptBlob) 2
Try-Cng "rebuilt-blob flags=0     " (Rebuild-BcryptBlob) 0
Try-Capi2 "keyx/aes " 0x0000A400 24
Try-Capi2 "sign/aes " 0x00002400 24
Try-Capi2 "keyx/full" 0x0000A400 1
Try-Capi2 "sign/full" 0x00002400 1
