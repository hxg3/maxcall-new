# عقد الجسر MaxCall — WebView2 (JS ⇄ C++)

> المرجع الأسلوبي: `CrmPopupDlg.cpp` — يستقبل C++ رسائل JSON عبر
> `ProcessWebViewMessage` بحقل `action` قيمه `save | dismiss`، ويخاطب JS عبر
> `ExecuteScript` بالدالتين `updateCallerInfo(number, name, notes, call_id)` و
> `notifySaveResult(bool)`. هذا العقد يوسّع نفس الأسلوب لشاشة `web-shell/`.

## 1) JS → C++ عبر `window.chrome.webview.postMessage`

كل رسالة كائن JSON بحقل `action: string` + حقول حسب الجدول.
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
| `setPresence` | `status` | `'available' \| 'busy' \| 'away' \| 'offline'` | حالة الحضور |

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
| `onCallState` | `(callId: number, state: 'idle' \| 'calling' \| 'ringing' \| 'active' \| 'held' \| 'ended')` | تحديث `#callState` |
| `onRegState` | `(registered: boolean, message: string)` | تحديث `#regDot` / `#regText` |
| `onMessage` | `(from: string, text: string)` | تنبيه / رسالة نصية |

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
| JS → C++ | `{action:'save', name: string, notes: string}` ثم `SaveCallerInfo()` → `POST /api/callers` |
| JS → C++ | `{action:'dismiss'}` → `ShowWindow(SW_HIDE)` |
| C++ → JS | `updateCallerInfo(number, name, notes, call_id)` |
| C++ → JS | `notifySaveResult(true \| false)` |

## 4) ملاحظات التنفيذ لـ C++

1. توسيع `ProcessWebViewMessage` بشروط `action == "makeCall" | "hangup" | ...` بنفس أسلوب `save/dismiss`.
2. `EscapeJson` لأي `string` يُحقن في `ExecuteScript` (أرقام/أسماء عربية).
3. إرسال `onRegState` عند تغيّر التسجيل و`onCallState` مع كل انتقال PJSUA.
