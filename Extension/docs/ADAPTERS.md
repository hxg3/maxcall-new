> **ملاحظة ميدانية (2026-11-04):** مواقع التأمين لم تعد تعمل على أجهزة
> الموظفين. كل التدفقات تُنفّذ بواسطة طبقة **الوساطة على السيرفر**
> (خدمات Python Playwright على `maxcall-broker`) — بيانات الدخول والجلسات
> تنتقل إلى السيرفر، والصيانة جزءّه من السيرفر وحده. أدوار الإضافة صارت
> متسلسلة تشبه ما كانت عليه لـNextCare: قراءة البطاقة + إرسال الطلب +
> عرض التقدّم + طباعة PDF. تفاصيل الوساطة مذكورة في `docs/SERVER_API.md`
> و`../../SERVER_REFERENCE.md` (§2.2 وسيط التأمين). المقال بالأسفل يظل
> مرجعاً تاريخياً لآلية "المحوّل الثاني" عند الحاجة.

# المحوّلات (Adapters) — دليل الكتابة والصيانة

المحوّل = ملف JS يُحقن في موقع شركة تأمين ويحوّل "زيارة بشرية" إلى خطوات مؤتمتة.
كل شركة ملف واحد مستقل تحت `src/content/insurers/` + سطر في `manifest.json` + مدخل في `src/lib/insurers.js`.

## عقد المحوّل

```js
window.__InsurerAdapter = {
  id: "nextcare",                    // يطابق id في lib/insurers.js
  async run(msg, bridge) { ... }     // يُستدعى بعد كل اكتمال تنقل للتبويب
};
```

`msg` (يرسله background مع كل إعادة إرسال):

| الحقل | المعنى |
|---|---|
| `card` | بطاقة المريض: `{patient:{name,phone,...}, insurance:{card_no,policy_no,...}, visit:{consultant,...}}` |
| `creds` | `{user, pass}` لهذه الشركة من الإعدادات |
| `autoPrint` | خيار الطباعة التلقائية |
| `stage` | المرحلة الحالية للمهمة: `null \| "after_login" \| "awaiting_result" \| "done" \| "error"` |

**قواعد ذهبية:**

1. المحوّل **بلا حالة** — يقرأ الصفحة + `stage` ويقرر. لا تعتمد على متغيرات باقية من استدعاء سابق.
2. بعد أي عملية تسبب تنقلاً (login/postback) → أرسل progress مناسب و**ارجع فوراً**؛
   الـbackground سيعيد الاستدعاء عند اكتمال التحميل.
3. كل خطوة ترسل `bridge.progress(step, status, detail)` — هذا ما يحدّث `stage` وواجهة الموظف.
4. لا تخزن مراجع عناصر عبر التنقلات؛ استعلم `document` من جديد كل مرة.

### خريطة stage

| تقرير المحوّل | stage الجديدة (في background) |
|---|---|
| `progress("login","ok")` | `after_login` |
| `progress("eligibility","ok")` أو `progress("submitted","ok")` | `awaiting_result` |
| `progress("document","ok")` | `done` |
| أي `status:"error"` | `error` |

## واجهة bridge (من `src/content/bridge.js`)

| العضو | الوصف |
|---|---|
| `sleep(ms)` | انتظار |
| `waitFor(fn, {timeout, poll})` | يعيد أول قيمة غير falsy من `fn` أو null عند انتهاء المهلة |
| `isVisible(el)` | ظهور فعلي (أبعاد + display/visibility) |
| `setNativeValue(el, v)` | تعيين قيمة عبر الـprototype setter + إطلاق `input`/`change` — تلتقطها كل الأطر |
| `progress(step, status, detail)` | تقرير تقدم للـbackground والـpopup |
| `fail(msg)` | يرمي خطأ "معالَج" — يظهر في اللوحة وفي تقرير التقدم |
| `overlay.show(title)` | لوحة الحالة العائمة (RTL) — تعيد `{root, title, body}` للإلحاق اليدوي |
| `overlay.step(text, kind)` | سطر خطوة: `run` (spinner) / `ok` / `error` — يعلّم السابق ✓ تلقائياً |
| `overlay.note(text)` | ملاحظة رمادية |
| `overlay.error(msg)` | سطر خطأ في اللوحة |
| `overlay.result({title, autoPrint})` | لوحة "المستند جاهز" + زر طباعة (+ عدّاد طباعة تلقائية) |
| `probe()` | **وضع الاستكشاف**: يسرد كل الحقول/الأزرار المرئية مع IDs في اللوحة و`console.table` |
| `ask(request)` | **سؤال الموظف في صفحة HIS**: `{kind:"captcha", image}` أو `{kind:"choice", options}` → يعيد `{value}` أو `{error}` |
| `captureNav({click,timeout})` | يلتقط رابط `window.open`/`target=_blank` الناتج عن النقر ويعيده (`{ok,url}`) بدل فتح نافذة (المتصفح يحظر `window.open` الآلي) — يُستخدم للانتقال داخل نفس التبويب |
| `diag(area,msg)` | يكتب سطراً في سجل الإضافة (popup → Copy log) — للتشخيص عند غياب الإشارات |

## وضع الاستكشاف (probe) — أداة رسم خرائط الشاشات

لما تدخل صفحة غير مُكتشفة (داشبورد MedNet مثلاً): افتح popup → "وضع الاستكشاف لصفحة التأمين".
ستظهر لوحة بكل العناصر المرشحة بصيغة:

```
input id=<b>pt1:itMemberNo</b> name=... ph="Member No" «Member Number *»
a id=<b>pt1:cbSearch</b> "Search"
```

انسخ المعرفات المطلوبة وحدّث دوال `find...` في المحوّل (انظر أدناه). الأداة نفسها تُستخدم للتشخيص
حين يفشل أي محوّل في إيجاد عنصر.

## NextCare (PULSE) — تفاصيل الحالية

تقنية: ASP.NET WebForms + Ajax.NET — **IDs ثابتة** (تغيّر نادراً بين الإصدارات).

| العنصر | المحدد | ملاحظة |
|---|---|---|
| نموذج الدخول | `#frmLogin` | وجوده = صفحة دخول |
| المستخدم/كلمة المرور | `#txtUserName` / `#txtPassword` | |
| التوكن | `#token` (hidden) | يتغير كل تحميل — يقرأ لحظة النداء فقط |
| نداء الدخول | `window.Login2.Login(user, pass, token, "1", cb)` | نفس ما تنفذه الصفحة؛ البديل: نقر `#btnLogin` |
| رسالة الخطأ | `#lblerrLogin` | يظهر أيضاً 429 (rate limit) |
| بنر V3 | `#btnSlider1` | يظهر إذا أجاب السيرفر `REDIRECT_TO_V3` |
| صفحة الاستعلام | `/EligibilityChecking2.aspx` | تعاد صياغتها في ثابت `ELIG` |

**صفحة الاستعلام مجهولة الـIDs حتى الجلسة الأولى** — الدوال المرشحة في `nextcare.js`:

- `findMemberInput`: أول `input` نصي مرئي يطابق `/member|card|certif|insur/i` في id/name/placeholder/aria-label.
- `findTypeSelect`: أول `select` مرئي (خيار "النوع").
- `findNextButton`: زر/رابط يطابق `/next|continue|متابعة|التالي/i`.

إن فشلت: المحوّل يعرض تلقائياً وضع الاستكشاف → حدّث الدوال (أو أضف selectors دقيقة أعلى الملف)
بعد جلسة حية واحدة، وبعدها يصبح التدفق كاملاً أوتوماتيكياً.

## MedNet — تفاصيل الحالية

تقنية: **Oracle ADF** على WebLogic — الصعوبة الكبرى:

- الـIDs تولّد بصيغة `container:component` (مثل `pt1:it2:`) وتتغير عند كل PPR —
  استخدم دائماً `[id$=":username"]`-style أو وظائف `find...` الزنة، ولا IDs كاملة ثابتة.
- الأزرار `partialSubmit` ترسل XMLHttpRequest جزئي — لا تعتمد على `form.onsubmit`.
- بعد كل عملية انتظر زوال الـmask: `waitFor(() => !document.querySelector(".AFMaskingPane"))`
  ثم استقرار حجم `document.body.innerHTML` (مُنفّذ في `adfIdle()`).
- الكوكي `MEDNEXT_ONLINE` يُنشأ من JS (صفحة loopback) — لا محاكاة fetch خارج المتصفح إطلاقاً.

المنفذ حالياً: كشف صفحة الدخول (أول input نصي + password + زر login) → دخول → `adfIdle`
→ **وضع استكشاف تلقائي** للداشبورد. الخطوة القادمة بعد جلسة حية: تحديد شاشة البحث عن العضوية
(تبويب → حقل البحث → زر Search → النتيجة → الطباعة) وتحويلها لمراحل مثل NextCare.

## Liva (NLI Gulf) — تفاصيل الحالية

تقنية: **بوابة FortiGate SSL-VPN** ثم **بوابة مزوّد Angular** تُقدَّم عبر مسار
proxy داخل نفس الأصل (`https://online.nlicgulf.com:12443/proxy/…/health.livainsurance.om/…`).

| المرحلة | المحدد | ملاحظة |
|---|---|---|
| دخول البوابة | `/remote/login?lang=en` | FortiGate: `input[name=username]` + `input[name=credential]` + زر Login |
| دخول المزوّد (نجاح) | `SUBMIT_KEY` في `sessionStorage` | بعد الإرسال يُتجاهل ادامه صفحة الدخول الانتقالية — لا كابتشا ثانية على دخول ناجح؛ إن بقي حقل الكابتشا بعد المهلة ⇒ فشل فعلي وإعادة محاولة (كابتشا جديدة + إعادة كتابة البيانات) |
| بطاقة المزوّد | `.card-title` نصّه "Provider" | تفتح تبويباً جديداً — `background` يسلّم المهمة (`handoverJob`) |
| كابتشا البوابة | `#CaptchaImage` + `#CaptchaInputText` | تُعرض الصورة في صفحة المشفى (`his-assist.js`) — يحلّها الموظف |
| رابط النموذج | `a` نصّه "Member Eligiblity & Online Claim Form" | `href` ينتهي بـ`MemberDetails/MemberDetails` |
| رقم العضوية | `#txtMemberId` | ثم النقر على زر البحث `#btnSearch` (الـEnter لا يشغّل البحث حسب الاختبار الحيوي) |
| الطبيب | `ul.ui-autocomplete li.ui-menu-item` | نكتب الاسم (بلا `Dr.`) حقلاً بعد حقل حتى تظهر القائمة ثم **نعنصر نقراً فعلياً** ويُثبَّت نصّه في الحقل |
| الهاتف | `#txtGsmNo` | حقل مقنّن (`968-`) — إدخال أصلي (`focus + insertText`) ثم تحقق أن الأرقام لزمت الحقل؛ احتياط `968-` + الرقم |
| طباعة الاستمارة | `#txtSpeciality` ثم `button.Printmember` | انتظار امتلاء حقل الاختصاص (تعبئة تلقائية AJAX بعد اختيار الطبيب) **قبل** الطباعة — إن لم يمتلئ خلال 14s نفشل برسالة واضحة |
| الهاتف | `#txtGsmNo` | محلي 8 أرقام (الموقع يضيف رمز الدولة) |
| الطباعة | `button.Printmember` | "Print Online Claim Form" → رابط PDF يُلتقط في عالم الصفحة |

نقاط خاصة:

1. **الكابتشا**: `bridge.ask({kind:"captcha", image})` يعرض الصورة في صفحة المشفى
   (لوحة `his-assist.js`) ويرسل الإجابة عبر الخلفية إلى تبويب التأمين.
   لا يوجد صفحة مشفى ⇒ تُكشف نافذة البوابة والموظف يكتب الكابتشا يدوياً.
2. **الطبيب**: يُطابَق مع اسم الطبيب من HIS (تجاهل `Dr.` وحالة الأحرف).
   لا مطابقة ⇒ تظهر قائمة الأسماء في صفحة المشفى ليختار الموظف.
3. **نمط رابط التقرير غير موثّق** ⇒ `report-capture.js` يلتقط أي رابط يُفتح أثناء
   مرحلة الطباعة، والخلفية تجرّب الجلب ثم اتّباع أي رابط PDF داخل صفحة HTML.
4. **تسليم المهمة**: بطاقة "Provider" تفتح تبويباً جديداً — `background.js`
   ينقل `jobs` إلى التبويب الجديد (`handoverJob`).
5. **البوابة Angular**: تُرسم بعد `document_idle` ⇒ المحوّل ينتظر ظهور إشارة
   حقيقية (`waitForKnownPage`, حتى 25ث) قبل الحكم على الصفحة. عند `unknown_page`
   يُفرّغ `dumpPage()` محتوى الصفحة (روابط/بطاقات/iframes) إلى سجل الإضافة
   عبر `bridge.diag` — أرسل اللوج لضبط المحددات بدقة.
6. **حظر النوافذ الآلية**: بطاقة "Provider" تفتح تبويباً بـ`window.open`، والمتصفح
   يرفضه لأن النقرة آلية (بلا تفاعل مستخدم). الحل: `bridge.captureNav` يلتقط
   الرابط ثم `location.href = url` داخل نفس التبويب — لا تبويب ضائع.
   (احتياط: `handoverJob` ينقل المهمة لو انفتح تبويب فعلاً.)

## قائمة إضافة شركة تأمين جديدة

1. أضف مدخلاً في `src/lib/insurers.js`: `id`, `label`, `payerPattern` (يطابق `payer_name`
   القادم من HIS), `urlPatterns`, `loginUrl`.
2. أنشئ `src/content/insurers/<id>.js` بعقد المحوّل أعلاه.
3. أضف كتلة `content_scripts` في `manifest.json` (matches + `bridge.js` ثم المحوّل) وأضف
   نطاق الموقع إلى `host_permissions`.
4. أضف حقلي user/pass في `src/options/` (HTML + JS).
5. اختبر بوضع الاستكشاف ثم أكمل المراحل تدريجياً (login → بحث → مستند).

## تصحيح الأخطاء

- سجلات المحوّل: DevTools داخل تبويب موقع التأمين (Console).
- سجلات background/popup: `chrome://extensions` → MaxCare Reception Assistant → **service worker** / Inspect popup.
- حالة المهمة الحالية: من Console الـpopup أرسل
  `chrome.runtime.sendMessage({type:"job:status", tabId: <id>}, console.log)`.
