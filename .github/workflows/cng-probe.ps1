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

public static class Check {
    static BigInteger FromBE(byte[] be) {
        byte[] le = new byte[be.Length + 1];
        for (int i = 0; i < be.Length; i++) le[i] = be[be.Length - 1 - i];
        le[be.Length] = 0;
        return new BigInteger(le);
    }
    static byte[] ToBE(BigInteger v, int len) {
        byte[] le = v.ToByteArray();
        byte[] be = new byte[len];
        for (int i = 0; i < len; i++) {
            int j = len - 1 - i;
            be[i] = (j < le.Length) ? le[j] : (byte)0;
        }
        return be;
    }
    public static string Manual(byte[] modBE, byte[] sig, byte[] expect) {
        BigInteger n = FromBE(modBE);
        BigInteger s = FromBE(sig);
        BigInteger m = BigInteger.ModPow(s, new BigInteger(65537), n);
        byte[] em = ToBE(m, 384);
        bool padOk = (em[0] == 0x00 && em[1] == 0x01);
        int sep = -1;
        for (int i = 2; i < em.Length; i++) { if (em[i] == 0x00) { sep = i; break; } }
        bool tailOk = false;
        if (sep > 0 && em.Length - sep - 1 == 32 + 19) {
            tailOk = true;
            for (int i = 0; i < 32; i++) if (em[em.Length - 32 + i] != expect[i]) { tailOk = false; break; }
        }
        string hex = "";
        for (int i = 0; i < 24; i++) hex += em[i].ToString("x2");
        return "em[0..1]=" + em[0].ToString("x2") + em[1].ToString("x2") + " separatorAt=" + sep
             + " tailMatchesDigest=" + tailOk + " head=" + hex;
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
