# تثبيت لمرة واحدة على جهاز الاستقبال — يشغَّل كمسؤول (Administrator PowerShell)
# بعدها: إعادة تشغيل Chrome — تُثبّت الإضافة وتتحدث تلقائياً من السيرفر (HTTPS بلا hash).

# 1) الثقة بشهادة السيرفر (تُنزّل عبر http لأن https لم يوثق بعد)
Invoke-WebRequest -Uri "http://maxcall.local:3001/ext/maxcall-local.crt" -OutFile "$env:TEMP\maxcall-local.cer"
Import-Certificate -FilePath "$env:TEMP\maxcall-local.cer" -CertStoreLocation Cert:\LocalMachine\Root

# 2) سياسة التثبيت الإجباري + التحديث التلقائي
$reg = "HKLM:\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist"
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty -Path $reg -Name "1" -Value "akoocbgimpeffcipdkhdamanfgbjpinl;https://maxcall.local:3443/ext/updates.xml"

Write-Host "تم — أعد تشغيل Chrome. للتحديث الفوري: chrome://extensions -> Update"
