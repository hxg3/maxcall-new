$ErrorActionPreference = 'Stop'

$h = Get-Content "UpdatePublicKey.h" -Raw
$b = $h.Substring($h.IndexOf('g_maxcallUpdatePublicKey[] = {'))
$blob = [byte[]]([regex]::Matches($b.Substring(0, $b.IndexOf('};')), '0x([0-9A-Fa-f]{2})') | ForEach-Object { [Convert]::ToByte($_.Groups[1].Value, 16) })
$cbExp = [BitConverter]::ToUInt32($blob, 8)
$cbMod = [BitConverter]::ToUInt32($blob, 12)
$modLE = [byte[]]$blob[($cbExp + 24)..($cbExp + 23 + $cbMod)]
$modBE = [byte[]]$modLE.Clone()
[Array]::Reverse($modBE)

$wc = New-Object System.Net.WebClient
$msg = $wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta")
$sigTxt = [System.Text.Encoding]::ASCII.GetString($wc.DownloadData("http://192.168.1.32:3001/updates/update.json.beta.sig")).Trim()
$sig = [Convert]::FromBase64String($sigTxt)
$digest = [System.Security.Cryptography.SHA256]::Create().ComputeHash($msg)
"digest: $(($digest | ForEach-Object { $_.ToString('x2') }) -join '')"

Add-Type -ReferencedAssemblies System.Numerics -TypeDefinition @"
using System;
using System.Numerics;
using System.Runtime.InteropServices;

public static class Check {
    public static string Manual(byte[] modBE, byte[] sig, byte[] expect) {
        BigInteger n = new BigInteger(modBE, isUnsigned: true, isBigEndian: true);
        BigInteger s = new BigInteger(sig, isUnsigned: true, isBigEndian: true);
        BigInteger m = BigInteger.ModPow(s, new BigInteger(65537), n);
        byte[] em = m.ToByteArray(isUnsigned: true, isBigEndian: true);
        int len = (n.GetBitLength() + 7) / 8;
        string hex = BitConverter.ToString(em).Replace("-", "").ToLowerInvariant();
        bool padOk = em.Length >= 3 + 32 && em[0] == 0x00 && em[1] == 0x01;
        bool tailOk = false;
        if (em.Length >= 32) {
            tailOk = true;
            for (int i = 0; i < 32; i++) if (em[em.Length - 32 + i] != expect[i]) { tailOk = false; break; }
        }
        return "modulus " + len + " bytes, recovered " + em.Length + " bytes, pkcs1prefix=" + padOk + ", digestTail=" + tailOk
             + ", emhead=" + hex.Substring(0, Math.Min(40, hex.Length));
    }
}
"@

"manual: $([Check]::Manual($modBE, $sig, $digest))"

$rsa = New-Object System.Security.Cryptography.RSACng
$p = New-Object System.Security.Cryptography.RSAParameters
$p.Modulus = $modBE
$p.Exponent = [byte[]]@(0x01, 0x00, 0x01)
$rsa.ImportParameters($p)
"dotnet RSACng: $($rsa.VerifyData($msg, $sig, [System.Security.Cryptography.HashAlgorithmName]::SHA256, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1))"

$rsa2 = New-Object System.Security.Cryptography.RSACryptoServiceProvider
$p2 = New-Object System.Security.Cryptography.RSAParameters
$p2.Modulus = $modBE
$p2.Exponent = [byte[]]@(0x01, 0x00, 0x01)
$rsa2.ImportParameters($p2)
"dotnet RSACSP : $($rsa2.VerifyHash($digest, '2.16.840.1.101.3.4.2.1', $sig))"
