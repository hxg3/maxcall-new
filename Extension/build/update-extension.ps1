# محدّث الإضافة الاحتياطي (الأفضل: زر التحديث داخل الإضافة — native host)
$dest = Join-Path $env:LOCALAPPDATA "MaxCare\Extension"
Invoke-WebRequest -Uri "http://maxcall.local:3001/ext/extension.zip" -OutFile "$env:TEMP\mcext.zip"
Expand-Archive -Path "$env:TEMP\mcext.zip" -DestinationPath $dest -Force
Write-Host "تم تحديث ملفات الإضافة إلى $dest"
Write-Host "ثم اضغط زر التحديث داخل الإضافة (أو أعد تحميلها من chrome://extensions)"
