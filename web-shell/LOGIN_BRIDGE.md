# عقد الجسر MaxCall — شاشة تسجيل الدخول `web-shell/login.html` (JS ⇄ C++)

> يوسّع عقد `web-shell/BRIDGE.md` (نفس القواعد: إرسال JS كنص `JSON.stringify`
> لأن C++ يقرأ عبر `TryGetWebMessageAsString`، والاستقبال عبر `ExecuteScript`)
> لاستبدال `LoginDlg` الأصلية بواجهة WebView2. المصدر الوحيد للصفحة هو
> `web-shell/login.html` — يُضمَّن في الـ exe بنفس أسلوب `res/main_shell.bin`
> (انظر `BRIDGE.md` §5).

## 1) كيف يعمل `LoginDlg` الحالي (المرجع الذي بُني عليه العقد)

1. البوابة عند الإقلاع: `microsip.cpp:315-316` — يعرض `LoginDlg::DoModal()`
   وإذا لم يرجع `IDOK` مع `loginSuccess == true` يخرج التطبيق (`return FALSE`).
2. التحقق المحلي: `LoginDlg.cpp:82-85` (`OnBnClickedLogin`) — يرفض المتابعة
   إذا `m_username` أو `m_password` فارغاً (`IDC_LOGIN_USERNAME` /
   `IDC_LOGIN_PASSWORD` عبر `DDX_Text` في `LoginDlg.cpp:39-40`).
3. طلب HTTP من C++ نفسه: `LoginDlg.cpp:94-102` — يبني
   `{"username":"…","password":"…"}` (مع `EscapeLoginJson` في
   `LoginDlg.cpp:13-27`) ويبعثه `POST` إلى
   `http://maxcare.local:3001/api/agent/login` عبر `URLGetSync`
   (المعرّفة في `global.*`).
4. الحسابات تأتي من الخادم لا من الإعدادات المحلية: عند `statusCode == 200`
   و `ok == true` يقرأ `extension` و `name` و `sip_server`
   (`LoginDlg.cpp:113-136`، والقيمة الاحتياطية `maxcare.local` في السطر 136)،
   ثم يمسح الحساب 1 (`accountSettings.AccountDelete(1)` في السطر 138)
   ويملأ `accountSettings.account` في الذاكرة فقط: `server`/`domain` =
   `sip_server`، `port = 5060`، `username`/`authID` = رقم الـ extension،
   `password` = كلمة المرور المدخلة، `displayName` = الاسم أو اسم المستخدم،
   `rememberPassword = false`، `transport = "udp"`، `accountId = 1`
   (السطور 140-149) — ثم `loginSuccess = true` و `EndDialog(IDOK)` (151-152).
   لا يُستدعى `SettingsSave()` هنا، وبما أن `rememberPassword = false`
   فكلمة المرور لا تُكتب على القرص (`settings.cpp:977`).
5. الأخطاء والتحميل: نص الحالة `IDC_LOGIN_STATUS` يعرض
   `Invalid username or password.` (السطر 156) أو
   `Connection failed. Check server address.` (السطر 160)،
   والتحميل عدّاد نقطي `SetTimer(1, 400)` (السطور 89-90 و `OnTimer`
   في 174-184). بعد الدخول يُنشأ `CmainDlg` (`microsip.cpp:321`) الذي
   يسجّل الحساب عبر `CmainDlg::PJAccountAdd()` (`mainDlg.cpp:3529`:
   يتجاوز إن لم يكن PJSUA يعمل أو `accountId == 0` أو اسم المستخدم فارغاً)
   ثم `PJAccountAddRaw()` (السطر 3549) التي تبني `pjsua_acc_config`
   وتستدعي `pjsua_acc_add` (السطر 3603).

## 2) JS → C++ عبر `window.chrome.webview.postMessage` (نص JSON)

| action | الحقول | الأنواع | الوصف |
|---|---|---|---|
| `login` | `username`, `password`, `server?` | `string`, `string`, `string?` | بيانات الدخول؛ `server` اختياري (افتراضي `maxcare.local` — نفس احتياطي `LoginDlg.cpp:136`) |

مثال (إلزامي كنص — لا ترسل كائناً خاماً):

```js
window.chrome.webview.postMessage(JSON.stringify({
  action: 'login', username: '101', password: 'secret', server: 'maxcare.local'
}));
```

قواعد:

1. تُقرأ الرسالة في C++ عبر `TryGetWebMessageAsString` ثم تُحلَّل كـ JSON
   (نفس نمط `CrmPopupDlg::ProcessWebViewMessage` في `CrmPopupDlg.cpp:309`
   و `MainShellDlg::ProcessShellMessage` في `MainShellDlg.cpp:319-330`).
2. أضف فرع `action == "login"` في `ProcessShellMessage`
   (`MainShellDlg.cpp:335-341` هو مكان فروع `shellReady/getData` الحالية،
   وبقية الأوامر في 342-451) — أو في مضيّف WebView2 الخاص بنافذة الدخول
   إن فُصلت عن `MainShellDlg`.
3. يملك C++ طلب HTTP (نفس `URLGetSync` + `EscapeLoginJson` في
   `LoginDlg.cpp:13-27`) — لا تبعث JS كلمة المرور لأي مكان آخر.
   عنوان الطلب: `http://<server>:3001/api/agent/login` (الأصل مثبّت على
   `maxcare.local` في `LoginDlg.cpp:99`؛ هنا `<server>` من حقل `server`).
4. تحقق C++ يعكس `LoginDlg.cpp:82-85`: ارفض `username`/`password`
   الفارغين عبر `onLoginError` بدل `AfxMessageBox`.
5. خارج WebView2 (فتح مباشر في متصفح) تعمل الصفحة بوضع fallback:
   `console.log('[MaxCallLogin:fallback]', text)` فقط ولا ترمي استثناءً
   (نفس سلوك `_postToNative` في `web-shell/app.js:8-25`).

## 3) C++ → JS عبر `ExecuteScript` (دوال عالمية على `window`)

| الدالة | التوقيع | الوصف |
|---|---|---|
| `onLoginError` | `(message: string)` | عرض نص الخطأ في `#loginError` (فارغ = إخفاء) |
| `onLoginBusy` | `(busy: boolean)` | تعطيل الحقول + الزر وإظهار/إخفاء `#loginSpinner` |

مثال من C++ (نفس نمط `MainShellDlg::ExecuteShellScript` في
`MainShellDlg.cpp:453-469` و `PushRegState` في 471-477):

```cpp
// بدء المحاولة
js.Format(_T("onLoginBusy(true)"));
// فشل
js.Format(_T("onLoginError('%s')"), EscapeJs(message));
js.Format(_T("onLoginBusy(false)"));
// نجاح: أغلق نافذة الدخول native وتابع الإقلاع — لا حاجة لدالة نجاح
```

قواعد:

1. غلّف أي `string` يُحقن في `ExecuteScript` عبر `MainShellDlg::EscapeJs`
   (`MainShellDlg.h:73`) كما تفعل `PushIncomingCall` (`MainShellDlg.cpp:479`).
2. التسلسل الإلزامي لكل محاولة: `onLoginBusy(true)` أولاً، ثم عند الانتهاء
   إما إغلاق النافذة native والمتابعة (نجاح — تعبئة `accountSettings`
   كما في `LoginDlg.cpp:138-149` ثم نفس مسار `microsip.cpp:321` +
   `PJAccountAdd` في `mainDlg.cpp:3529`)، أو `onLoginError(…)` +
   `onLoginBusy(false)` (فشل).
3. خريطة رسائل الخطأ (مطابقة لنصوص `LoginDlg.cpp:156,160` بالإنجليزية):
   بيانات خاطئة ← `Invalid username or password.`، تعذّر الاتصال ←
   `Connection failed. Check server address.`، بلا extension ←
   `The account has no assigned extension.`.

## 4) عناصر DOM التي يعتمد عليها العقد (`login.html`)

| id | النوع | ملاحظة |
|---|---|---|
| `loginUser` | `input` | Extension / Username (يرسل كـ `username`) |
| `loginPass` | `input[type=password]` | يرسل كـ `password` |
| `loginServer` | `input` | اختياري، القيمة الافتراضية `maxcare.local` |
| `btnSignIn` | `button[type=submit]` | يعطَّل أثناء `onLoginBusy(true)` |
| `loginBtnLabel` | `span` | `Sign in` / `Signing in…` |
| `loginSpinner` | `svg.animate-spin` | يظهر أثناء `onLoginBusy(true)` |
| `loginError` | `p[role=alert]` | سطر الخطأ (`hidden` عند الفراغ) |
| `bridgeStatus` | `p` | سطر تشخيص فقط (لا يعتمد عليه C++) |
