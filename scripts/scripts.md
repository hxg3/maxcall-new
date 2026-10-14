# MaxCall Scripts

## release-old (المستقرة — مجمّدة)
**What it does:** النسخة القديمة المستقرة (`maxcall-microsip-full` → repo `hxg3/maxcall-microsip`). تُستخدم فقط للتراجع أو إصدار طارئ.
**Command:** `./release-old.sh`
**Without questions:** `./release-old.sh "commit message" --no-watch`

## release (النشطة — قيد التطوير)
**What it does:** النسخة الجديدة (`maxcall-new` → repo `hxg3/maxcall-new`). كل شيء عبر قوائم مرقّمة. السؤال الأول: لمن هذه النسخة؟

- `1) Beta` — حفظ + دفع `main` فقط، بلا وسم. النسخة تصل لقناة `beta` وحدها، تراها أجهزتك أنت فقط بعد تحويل الجهاز من قائمة الدرج إلى `Beta builds`.
- `2) All` — نفس الخطوات + تحديث `const.h` + رفع وسم `vX.Y.Z`، فيصل التحديث لكل المستخدمين على القناة المستقرة.

السكربت يتجاهل أي وسم ليس له إصدار منشور، ويخبرك بذلك بدل أن يقفز فوق الأرقام بصمت. كما يعرض `Download` و `Status` و `Verify`.

**Command:** `./release.sh` — أو `./release.sh beta` / `./release.sh all` لتختار الجمهور مسبقاً.

**المرآة الداخلية (تلقائية):** بعد كل نشر ينسخ السكربت `update.json` و`update.json.sig` الموقّعين إلى `scripts/mirror/`. على شبكة لا تستطيع إكمال TLS مع GitHub، ينتقل العميل إلى `http://maxcall.local:3001/updates` تلقائياً — بلا أي إعداد من المستخدم. لوضع المجلد في مكان آخر:
`MAXCALL_MIRROR_DIR=/var/www/maxcall/updates ./release.sh beta`
ثم اجعل خادمك يقدّم ذلك المجلد على المسار `/updates`.

**بدون أسئلة:** رسالة الالتزام يجب أن تسبقها `--msg` صراحةً، حتى لا يتحول رقم عابر إلى رسالة commit:
`./release.sh beta --msg "fix(call): drop the echo" --no-watch`

**ملاحظة مهمة:** التطبيق يحتاج أن يكون داخل مجلد **قابل للكتابة**. في مجلد بصلاحيات قراءة فقط يفشل استخراج ملفات النغمة في `res\` وقد تظهر شاشة فارغة. استخدم مجلداً عادياً بلا قيود، وتأكد أن `MaxCall.exe.old` أو `MaxCall.updating.exe` لم يتبقَّ من تحديث متوقف.

## التراجع للنسخة القديمة
1. ثبّت نسخة `MaxCall.exe` من ريليس `hxg3/maxcall-microsip` الأخير (`v5.3.5`).
2. أي إصلاح طارئ على القديمة يتم عبر `./release-old.sh` فقط.
3. لا تدمج أبداً بين الريبوين — كل واحد مستقل تماماً.

## diagnose-update (فحص فشل التحديث)
**What it does:** يجمع تقريراً عن جهاز ويندوز عندما لا يستطيع MaxCall الوصول إلى قناة التحديث. يُشخّص الساعة، وDNS، وTLS، والبروكسي، وجرّب الطلب نفسه عبر WinInet (نفس الحزمة التي يستخدمها التطبيق)، وينسخ آخر `maxcall_debug.log`.
**Command:** انسخ `diagnose-update.ps1` إلى جهاز ويندوز ثم:
`powershell -NoProfile -ExecutionPolicy Bypass -File .\diagnose-update.ps1`
**الناتج:** ملف `maxcall-update-diagnosis.txt` في نفس المجلد — أرسله ويصبح السبب واضحاً بلا تخمين.
