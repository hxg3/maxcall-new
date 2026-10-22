# عقد الجسر MaxCall — WebView2 (JS ⇄ C++)

> المرجع الأسلوبي: `CrmPopupDlg.cpp` — يستقبل C++ رسائل JSON عبر
> `ProcessWebViewMessage` بحقل `action` قيمه `save | dismiss`، ويخاطب JS عبر
> `ExecuteScript` بالدالتين `updateCallerInfo(number, name, notes, call_id)` و
> `notifySaveResult(bool)`. هذا العقد يوسّع نفس الأسلوب لشاشة `web-shell/`.

## 1) JS → C++ عبر `window.chrome.webview.postMessage`

كل رسالة كائن JSON بحقل `action: string` + حقول حسب الجدول.
تُرسل دائماً كنص `JSON.stringify(msg)` — لأن C++ يقرأ عبر
`TryGetWebMessageAsString` (نفس نمط نافذة CRM المُثبت). إرسال كائن خام
غير مدعوم من طرف C++.
عند غياب WebView2 (فتح عادي في متصفح) يعمل `MaxCallBridge` بوضع fallback:
`console.log` فقط ولا يرمي استثناء.

| action | الحقول | الأنواع | الوصف |
|---|---|---|---|
| `makeCall` | `number` | `string` | بدء مكالمة صادرة |
| `hangup` | `callId?` | `number` (`-1` = الحالية) | إنهاء المكالمة |
| `answer` | `callId?` | `number` | الرد على الواردة |
| `hold` | `callId?`, `on` | `number`, `boolean` | تعليق (`true`) / استئناف (`false`) |
| `transfer` | `callId?`, `target` | `number`, `string` | تحويل blind لرقم |
| `sendDTMF` | `callId?`, `digit` | `number`, `string` (`0-9*#`) | نغمة أثناء المكالمة |
| `setPresence` | `status` | `'available' \| 'busy' \| 'away' \| 'offline'` | حالة الحضور (انظر التعيين أدناه) |
| `shellReady` | — | — | ترسلها الواجهة تلقائياً عند التحميل؛ يرد C++ بلقطة كاملة |
| `getData` | — | — | طلب اللقطة الكاملة يدوياً (زر Retry): جهات + حساب + تسجيل + مكالمة |
| `openSettings` | — | — | فتح نافذة الإعدادات الأصلية |
| `quitApp` | — | — | خروج من التطبيق |
| `minimizeApp` | — | — | تصغير نافذة القشرة |
| `openPopup` | `number` | `string` | فتح بطاقة المتصل لنفس الرقم (يدوياً من Dial/Contacts/Log) |
| `pinToggle` | `on` | `boolean` | تثبيت النافذة فوق الكل (يُحفظ في الإعدادات) |
| `checkUpdate` | — | — | فحص التحديثات يدوياً الآن (زر Retry عند الخطأ) |
| `installUpdate` | — | — | بدء تنزيل وتثبيت التحديث المعلَن (زر `#btnUpdateNow`) |
| `dismissUpdate` | — | — | إخفاء بانر التحديث لهذه الجلسة فقط — لا يُلغي تنزيلاً جارياً ولا يغيّر حالة C++ (`onUpdateState` التالية تُظهره مجدداً) |
| `feedbackQueue` | — | — | طلب قائمة مهام التغذية الراجعة من C++ (الذي يجلبها من السيرفر عبر الجسر) |
| `feedbackClaim` | `taskId` | `string` | ادعاء مهمة تغذية راجعة ⇒ تُصبح in_progress وتُسنَد للموظف |
| `feedbackRespond` | `taskId`, `outcome`, `score?`, `categories?`, `comment?`, `duration_sec?`, `next_attempt_at?` | — | تسجيل استجابة على مهمة (done / no_answer / refused / wrong_number / callback_later / not_applicable) |
| `feedbackSkip` | `taskId` | `string` | تخطي مهمة نهائياً ⇒ status='skipped' |

> `setPresence` يُعيَّن في `MainShellDlg::ProcessShellMessage` كالتالي:
> `available/away` → إلغاء DND + نشر متاح، `busy` → تفعيل DND + نشر متاح،
> `offline` → نشر غير متصل. (لا توجد واجهة Away مميزة في PJSUA.)
>
> `shellReady` → يستدعي `PushSnapshot()` التي ترسل `onRegState` الحالية
> ثم حالة المكالمة النشطة إن وجدت.

مثال:

```js
window.chrome.webview.postMessage({ action: 'makeCall', number: '0551234567' });
window.chrome.webview.postMessage({ action: 'hold', callId: -1, on: true });
```

## 2) C++ → JS عبر `ExecuteScript`

دوال عالمية على `window` (يعرّفها `app.js` ويفوّضها لـ `MaxCallBridge`):

| الدالة | التوقيع | الوصف |
|---|---|---|
| `onIncomingCall` | `(number: string, name: string, callId: number)` | إظهار بانر الواردة |
| `onCallState` | `(callId: number, state: 'idle' \| 'calling' \| 'ringing' \| 'active' \| 'held' \| 'ended', number: string, name: string)` | تحديث شريط المكالمة (الرقم/الاسم فارغان في أحداث التعليق — تُعاد القيم المخزنة) |
| `onRegState` | `(registered: boolean, message: string)` | تحديث `#regDot` / `#regText` + إخفاء بانر المحرك |
| `onMessage` | `(from: string, text: string)` | تنبيه / رسالة نصية |
| `onContacts` | `(jsonText: string)` | مصفوفة JSON `[{name, number, presence}]` من دفتر `pageContacts` |
| `onAccount` | `(user: string, domain: string, name?: string)` | سطر الحساب في الهيدر بصيغة `user-name` |
| `onPinState` | `(on: boolean)` | حالة زر التثبيت (تُدفع مع كل لقطة) |
| `onCallHistory` | `(jsonText: string)` | سجل السيرفر: `{rows:[{time,number,name,direction,status,duration,answered_by,has_rec}]}` من `GET /api/calls/history` — يُجلب عند `shellReady/getData` وبعد كل إنهاء بـ 4 ثوانٍ |
| `onFeedbackQueue` | `(jsonText: string)` | قائمة مهام التغذية الراجعة من السيرفر: `{ok,data:[{id,phone,patient_name,visit_date,doctor,payer,visit_amount,status,attempts,max_attempts}],count}` — تُستدعى عند فتح تبويب تاسك / after each action |
| `onUpdateState` | ... |

مثال من C++ (نفس نمط `UpdateWebView` / `OnCrmSaveResult`):

```cpp
// واردة جديدة
js.Format(_T("onIncomingCall('%s', '%s', %d)"), escapedNumber, escapedName, call_id);
// تغيّر الحالة
js.Format(_T("onCallState(%d, '%s')"), call_id, _T("active"));
```

## 3) العقد القديم (قائم — للتوافق)

| الاتجاه | الشكل |
|---|---|
| JS → C++ | `{action:'save', name: string, notes: string, file_no?: string, phone2?: string, relation?: 'patient'\|'relative'\|'other'}` ثم `SaveCallerInfo()` → `POST /api/callers` |
| JS → C++ | `{action:'dismiss'}` → `ShowWindow(SW_HIDE)` |
| C++ → JS | `updateCallerInfo(number, name, notes, call_id, extra?: {file_no, phone2, relation})` |
| C++ → JS | `notifySaveResult(true \| false)` |

## 3.1) عقد قسم Patient File (WebHIS — سجل المريض في النافذة المنبثقة)

المصدر: `GET /api/his/caller/<phone>` عبر `his_broker.py` في السيرفر — قراءة فقط
من Solver WebHIS، بتوثيق الموظف نفسه (Basic auth من `accountSettings.agentLoginName`
+ `account.account.password`). فشل التوثيق أو انقطاع الشبكة يُخفى القسم بهدوء
(دون toast) ويبقى ما عدا قسم المريض كما كان.

توقيت الجلب: يبدأ مع كل `OnWebViewReady` (بناء النافذة لرقم جديد) **وكل
`Restore()`** (إعادة فتح نفس الرقم، لأن `updateCallerInfo` يفرّغ القسم داخلياً)؛
ويظهر أثناء الجلب مؤشر انتظار قصير (`hisLoading`) يُستبدل بالبطاقة عند وصولها.

| الاتجاه | الشكل |
|---|---|
| C++ → JS | `hisLoading()` — مؤشر انتظار صادر فور بدء الجلب (يُستبدل بالبطاقة أو يُخفى بهدوء) |
| C++ → JS | `updateHisInfo('<json>')` — `EscapeJson` ثم حقن نصي: `{ok, matched, matches:[{pat_id,name,age,sex,phone}...], selected:{...}, visits:[...], bills:[...], source_url}` |
| C++ → JS | `hisUnavailable()` — إخفاء صامت (انقطاع/توثيق مفقود) |
| JS → C++ | `{action:'his_search', by:'name'\|'id', q: string}` — بديل البحث عند عدم المطابقة، يمرر إلى `LoadHisInfo('?by=..&q=..')` |
| JS → C++ | `{action:'his_select', pat_id: string}` — اختيار مريض من عدة مطابقات → `LoadHisInfo('?pat_id=..')` |
| JS → C++ | `{action:'open_webhis'}` — نسخ الرقم للحافظة وفتح `http://solverserver:81/#/home/hmis/op/PatientRegistration` في المتصفح الافتراضي للموظف (جلسته الخاصة). الحقل في الواجهة هو الزر **Open File** (بقسم البطاقة ونموذج البديل) |

## 4) ملاحظات التنفيذ لـ C++

1. توسيع `ProcessWebViewMessage` بشروط `action == "makeCall" | "hangup" | ...` بنفس أسلوب `save/dismiss`.
2. `EscapeJson` لأي `string` يُحقن في `ExecuteScript` (أرقام/أسماء عربية).
3. إرسال `onRegState` عند تغيّر التسجيل و`onCallState` مع كل انتقال PJSUA.

## 5) التضمين في الـ exe (مطبَّق)

* المصدر الوحيد هو `web-shell/` — لا تعدّل `res/main_shell.bin` يدوياً.
* التوليد: `python3 tools/build_shell_bin.py` (يدمج `app.js` داخل `index.html`
  ويضيف إشعار `shellReady` التلقائي) → يكتب `res/main_shell.bin`.
* `res/embedded.rc2` يضمّنه كمورد `IDR_MAIN_SHELL_HTML`، وخط الـ CI يعيد
  توليده قبل كل بناء (`Build Web Shell bundle`).
* المضيف: `MainShellDlg` (نافذة `IDD_MAIN_SHELL`) — يفتح تلقائياً عند الإقلاع
  إذا `modernUI=1` (الافتراضي للثبيتات الجديدة)، ويُبدَّل من قائمة `Modern UI`.

## 6) فحص قائمة التغذية الراجعة بلا واجهة (`--test-feedback`)

`MaxCall.exe --test-feedback` ينفّذ **نفس طلب تبويب Tasks بالضبط** بلا فتح
واجهة: يبني ترويسة `Authorization: Basic` من نفس بيانات دخول الموظف
(`accountSettings.agentLoginName` + `accountSettings.account.password`)،
يطلب `GET /api/feedback/queue`، ثم يكتب `feedback_test_result.json`:

```json
{"ok":true,"status":200,"tasks":3,"note":""}
```

* بيانات الاعتماد تُقرأ من `MAXCALL_TEST_USER` / `MAXCALL_TEST_PASS` فلا يمرّ
  سرّ على سطر الأوامر.
* `ok=true` تعني: السيرفر ردّ 200 **وقائمة المهام ليست فارغة** — أي أن تبويب
  Tasks سيعرض مهام فعلاً.
* المهمة `Feedback Queue Test` على جهاز ويندوز (`self-hosted`) تشغّل هذا الفحص
  على البناء المنشور في المرآة، فأي كسر في المصادقة أو الجسر يفشل قبل أن
  يصل إلى الموظف.
