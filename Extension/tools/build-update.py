#!/usr/bin/env python3
"""بناء حزمة التحديث التلقائي للـextension (CRX + updates.xml) ورفعها للسيرفر.

الاستخدام:
  python3 tools/build-update.py            # بناء فقط
  python3 tools/build-update.py --deploy   # بناء + رفع إلى maxcall.local

- يجمّع نسخة نظيفة (manifest + src + icons) في build/stage
- يوقّعها بالمفتاح tools/extension-key.pem (لا يُرفع ولا يُCommit أبداً)
- يحسب SHA256 للـCRX (يُستخدم اختيارياً في سياسة Windows)
- يكتب build/updates.xml ويحدّث build/install-on-windows.ps1 بالـID والهاش
"""
import base64
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
STAGE = BUILD / "stage"
KEY = ROOT / "tools" / "extension-key.pem"
BASE_URL = "https://maxcall.local:3443/ext"
CERT_HTTP_URL = "http://maxcall.local:3001/ext/maxcall-local.crt"  # لتنزيل الشهادة قبل الثقة بها
CRX_NAME = "maxcare-reception.crx"
SERVER = "root@maxcall.local"
SERVER_DIR = "/opt/maxcall-dashboard/ext-mirror"


def ext_id_from_key() -> str:
    der = subprocess.run(
        ["openssl", "rsa", "-in", str(KEY), "-pubout", "-outform", "DER"],
        capture_output=True, check=True,
    ).stdout
    h = hashlib.sha256(der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in h)


def stage_sources():
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    shutil.copy2(ROOT / "manifest.json", STAGE / "manifest.json")
    shutil.copytree(ROOT / "src", STAGE / "src")
    shutil.copytree(ROOT / "icons", STAGE / "icons")


def pack_crx() -> Path:
    crx = BUILD / "stage.crx"
    crx.unlink(missing_ok=True)
    profile = BUILD / "pack-profile"
    if profile.exists():
        shutil.rmtree(profile)
    r = subprocess.run(
        ["google-chrome", "--no-sandbox", "--headless",
         f"--user-data-dir={profile}",
         f"--pack-extension={STAGE}",
         f"--pack-extension-key={KEY}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not crx.exists():
        sys.exit(f"chrome pack failed ({r.returncode}):\n{r.stderr[-800:]}\n{r.stdout[-400:]}")
    out = BUILD / CRX_NAME
    shutil.move(crx, out)
    return out


def write_updates_xml(version: str) -> Path:
    appid = ext_id_from_key()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">\n'
        f'  <app appid="{appid}">\n'
        f'    <updatecheck codebase="{BASE_URL}/{CRX_NAME}" version="{version}" />\n'
        "  </app>\n"
        "</gupdate>\n"
    )
    p = BUILD / "updates.xml"
    p.write_text(xml)
    return p


def write_local_updater(version: str):
    """حزمة التحديث: zip + version.json (يفحصه الـpopup) + سكربت المجلد (بديل احتياطي).
    المسار الأساسي للتحديث الذاتي هو native host في Extension/native-host/."""
    zip_path = BUILD / "extension.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_path.with_suffix("")), "zip", STAGE)
    (BUILD / "version.json").write_text(json.dumps({"version": version}) + "\n")
    ps = f"""# محدّث الإضافة الاحتياطي (الأفضل: زر التحديث داخل الإضافة — native host)
$dest = Join-Path $env:LOCALAPPDATA "MaxCare\\Extension"
Invoke-WebRequest -Uri "http://maxcall.local:3001/ext/extension.zip" -OutFile "$env:TEMP\\mcext.zip"
Expand-Archive -Path "$env:TEMP\\mcext.zip" -DestinationPath $dest -Force
Write-Host "تم تحديث ملفات الإضافة إلى $dest"
Write-Host "ثم اضغط زر التحديث داخل الإضافة (أو أعد تحميلها من chrome://extensions)"
"""
    (BUILD / "update-extension.ps1").write_text(ps, encoding="utf-8-sig")


def write_install_ps(extension_id: str, crx_hash: str):
    ps = f"""# تثبيت لمرة واحدة على جهاز الاستقبال — يشغَّل كمسؤول (Administrator PowerShell)
# بعدها: إعادة تشغيل Chrome — تُثبّت الإضافة وتتحدث تلقائياً من السيرفر (HTTPS بلا hash).

# 1) الثقة بشهادة السيرفر (تُنزّل عبر http لأن https لم يوثق بعد)
Invoke-WebRequest -Uri "{CERT_HTTP_URL}" -OutFile "$env:TEMP\\maxcall-local.cer"
Import-Certificate -FilePath "$env:TEMP\\maxcall-local.cer" -CertStoreLocation Cert:\\LocalMachine\\Root

# 2) سياسة التثبيت الإجباري + التحديث التلقائي
$reg = "HKLM:\\SOFTWARE\\Policies\\Google\\Chrome\\ExtensionInstallForcelist"
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty -Path $reg -Name "1" -Value "{extension_id};{BASE_URL}/updates.xml"

Write-Host "تم — أعد تشغيل Chrome. للتحديث الفوري: chrome://extensions -> Update"
"""
    (BUILD / "install-on-windows.ps1").write_text(ps, encoding="utf-8-sig")


def main():
    if not KEY.exists():
        sys.exit(f"مفتاح غير موجود: {KEY}")
    manifest = json.loads((ROOT / "manifest.json").read_text())
    version = manifest["version"]

    stage_sources()
    crx = pack_crx()
    crx_hash = hashlib.sha256(crx.read_bytes()).hexdigest()
    write_updates_xml(version)
    write_local_updater(version)
    write_install_ps(ext_id_from_key(), crx_hash)

    print(f"version      : {version}")
    print(f"extension id : {ext_id_from_key()}")
    print(f"crx          : {crx} ({crx.stat().st_size} bytes)")
    print(f"crx sha256   : {crx_hash}")
    print(f"updates.xml  : {BUILD / 'updates.xml'}")
    print(f"local updater: {BUILD / 'extension.zip'} + version.json + update-extension.ps1")

    if "--deploy" in sys.argv:
        subprocess.run(["ssh", SERVER, f"mkdir -p {SERVER_DIR}"], check=True)
        host_files = [str(ROOT / "native-host" / f) for f in ("mc-host.ps1", "mc-host.cmd", "setup-updater.ps1")]
        subprocess.run(
            ["scp", str(crx), str(BUILD / "updates.xml"),
             str(BUILD / "extension.zip"), str(BUILD / "version.json"),
             str(BUILD / "update-extension.ps1"),
             *host_files,
             f"{SERVER}:{SERVER_DIR}/"],
            check=True,
        )
        # الخدمة تعمل بمستخدم maxcall — الملفات يجب أن تكون مقروءة (بما فيها forms/)
        subprocess.run(["ssh", SERVER, f"chmod -R 755 {SERVER_DIR} && find {SERVER_DIR} -type f -exec chmod 644 {{}} \\;"], check=True)
        print(f"deployed     : {SERVER}:{SERVER_DIR}")


if __name__ == "__main__":
    main()
