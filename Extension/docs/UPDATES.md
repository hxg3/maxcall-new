# التحديث التلقائي للـExtension

الهدف: لا إرسال ملفات يدوياً لأجهزة Windows — الأجهزة تسحب التحديث من سيرفر MaxCare.

## واقع سياسات كروم (مُختبَر 2026-10)

- `ExtensionInstallForcelist` من رابط **http**: يرفضه كروم 88+ بدون hash، والhash
  يتغير مع كل إصدار → غير عملي.
- `ExtensionInstallForcelist` من **https**: محجوب `[BLOCKED]` على الأجهزة **غير المُدارة**
  (Workgroup بلا AD/MDM) — أجهزة الرسبشن كذلك. المسار يعمل فقط على أجهزة منضمة لنطاق
  أو مُدارة بالمؤسسة.
- **الإضافة غير المعبأة (unpacked) لا تتأثر بأي من هذا** — تعمل دائماً، وتحتاج
  تحديث ملفات + إعادة تحميل.

## المسار المعتمد الآن: تحديث ذاتي بنقرة (Native Messaging Host)

```
tools/build-update.py --deploy
        │ يبني extension.zip + version.json
        ▼
maxcall.local:3001/ext/  (extension.zip, version.json, mc-host.*, setup-updater.ps1)
        ▼
setup-updater.ps1 — مرة واحدة لكل جهاز (بلا صلاحيات مسؤول):
  ينزّل الإضافة إلى %LOCALAPPDATA%\MaxCare\Extension
  ويسجّل native host في HKCU\...\NativeMessagingHosts\com.maxcare.updater
        ▼
لكل إصدار جديد: الـpopup يعرض شريط "يتوفر إصدار جديد" → نقرة زر "تحديث" →
الإضافة تنادي mc-host عبر chrome.runtime.sendNativeMessage →
host ينزّل الـzip ويستبدل المجلد → chrome.runtime.reload() → النسخة الجديدة تعمل
```

نفس نمط التحديث الذاتي في MaxCall — بلا سكربتات يدوية ولا صلاحيات إدارية.

### التثبيت الأول على جهاز الاستقبال (مرة واحدة)

```powershell
Invoke-WebRequest -Uri "http://maxcall.local:3001/ext/setup-updater.ps1" -OutFile "$env:TEMP\setup-updater.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\setup-updater.ps1"
```
ثم: `chrome://extensions` → Developer mode → **Load unpacked** →
اختر المجلد المعروض (`%LOCALAPPDATA%\MaxCare\Extension`).

إن طلب السكربت معرفاً يدوياً (نادر — اختلاف حالة أحرف المسار): انسخ المعرف
من `chrome://extensions` وأعد التشغيل بـ `setup-updater.ps1 -ExtensionID <المعرف>`.

### بعدها — مع كل إصدار جديد

- أنا: `python3 tools/build-update.py --deploy` (يرفع version.json + zip)
- الموظف: يفتح الـpopup → شريط أصفر "يتوفر إصدار جديد" → زر **تحديث** → انتهى

بديل احتياطي (إن فشل native host): `update-extension.ps1` يحدّث المجلد يدوياً
ثم زر التحديث أو إعادة التحميل من `chrome://extensions`.

## المسار المستقبلي: Chrome Web Store (unlisted)

النشر في المتجر (غير مدرج — لا يظهر بالبحث) يجعل التحديث تلقائياً 100% من جوجل
بلا سكربتات، ويتطلب: حساب ناشر (رسوم 5$ لمرة واحدة) + مراجعة كروم. عند توفّره
يُرفع نفس مجلد `build/stage` كاملاً. الأسرار تبقى في الإعدادات المحلية كما هي.
