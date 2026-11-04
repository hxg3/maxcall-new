# مرجع سيرفر MaxCall — VM + VitalPBX + API + قواعد البيانات

> ## التزام المزامنة الإلزامي
> **أي تغيير أو تعديل أو إضافة على السيرفر (إعدادات، VitalPBX، الـ API،
> قاعدة البيانات، الشبكة، النسخ الاحتياطي) يجب توثيقه في هذا الملف
> فوراً وبشكل مباشر في نفس المهمة — قبل إعلان اكتمالها.**
> هذا الملف هو المرجع الموثوق الوحيد عن الـ VM والسيرفر وقاعدة البيانات،
> وأي انحراف عنه يُعتبر خللاً في المهمة نفسها وليس في التوثيق.
> سجّل كل تغيير في جدول «سجل التغييرات» أسفل الملف مع التاريخ والوصف.

## 0. نظرة عامة

| البند | القيمة |
|---|---|
| الدور | سيرفر الاتصالات المركزي (SIP + لوحة المشرف + API) |
| الـ VM | `VitalPBX-MaxCare` (ملفاتها في جذر هذا المشروع: `.vbox` + `.vmdk`) |
| المضيف | `maxcall` — Debian 12 (bookworm)، `2 CPU / 2GB RAM / 19GB disk (20% used)` |
| عناوين IP | **لا يُعتمد أي عنوان رقمي:** الوصول دائماً بالاسم `maxcall.local`. العنوان يُوزّع بـ DHCP على `enp1s0` وقابل للتغيّر، والعنوان الحالي `192.168.1.32` للعلم فقط — أي إعداد يستخدم رقماً يتوقف عند أول تغيير. (عناوين `192.168.1.165` و`192.168.56.10` تخصّ الـ VM القديمة المتقاعدة) |
| الوصول الإداري | SSH root + VitalPBX Admin على `https://maxcall.local` (بيانات الدخول عند الإدارة — لا تُكتب هنا أبداً) |
| اسم mDNS | `maxcall.local` (avahi من اسم المضيف) |
| الحالة | يعمل مستقر (uptime طبيعي، API بلا أخطاء آخر 24h) |

## 1. VitalPBX

| البند | القيمة |
|---|---|
| الإصدار | `vitalpbx 4.5.3-6` + `vitalpbx-api 4.5.1-1` |
| لوحة الإدارة | `https://maxcall.local` (ports `80/443`) |
| Asterisk | `20.19.0` — خدمة `asterisk.service` (running) |
| ملفات الإعداد | `/etc/asterisk/vitalpbx/` (تُدار من اللوحة — لا تعديل يدوي إلا للطوارئ) |
| الامتدادات | `101/Agent1`، `102/Agent2`، `103/Agent3` (جدول `ombutel.ombu_extensions`) |
| امتدادات الاختبار | `777` (يحاكي متصل خارجي `77181992`) و `778` (يحاكي `93744242`) — يصبان في تدفق `maxcall-in` |
| Trunks | `Virtual_GSM_Gateway` (pjsip → `localgw` على `192.168.1.199:5065`) — Trunk `Twilio_Easy_Trunk` **حُذف نهائياً 2026-09-06** (DB + pjsip + سياقات `trk-1/*`، وأُعيد توجيه مسارات الخروج إلى `trk-2`) |
| مسار الاختبار القديم | بادئة `9` (`_9X.` نحو Twilio) **حُذفت 2026-09-06** مع Trunk Twilio |
| Queues | طابور `maxcall-q` (منذ 2026-09-06): `ringall` بدورات 25s + `retry 2s` + `wrapup 5s` + `ringinuse=no` + المتصل ينتظر دائماً (`joinempty=yes`) — الأعضاء من الامتدادات تلقائياً (`_queue_sync` عند الإضافة/التغيير) + إيقاف مؤقت (`pause/unpause` من الداشبورد) + سجل `queues_log` عبر ODBC (ENTERQUEUE/CONNECT/ABANDON/PAUSE) |
| تدفق الوارد | ملف مخصص `extensions__99-maxcall.conf` — سياق `[maxcall-in]`: ترحيب `custom/WelcomeMaxcall3cx` ثم تسجيل `MixMonitor` ثم **`Queue(maxcall-q,tT)`** — الحلقة اللانهائية **مقصودة** (لا voicemail، يعمل 24/7 بلا ساعات عمل) |
| سجل الـ Queues | `queues_log` يحتوي فقط صفوف `QUEUESTART` — لا CONNECT/ABANDON قبل إنشاء Queue فعلية |

## 2. تطبيق الـ API (منفذ 3001)

| البند | القيمة |
|---|---|
| المسار | `/opt/maxcall-dashboard/app.py` (المصدر المعتمد في `server/app.py` بهذا الريبو — checksums متطابقة) |
| مرآة التحديث | `GET /updates/<name>` — عامة بلا مصادقة، تقدّم ٦ أسماء مسموحة فقط (`update.json` + `.sig` + `.beta` + `.beta.sig` + `MaxCall.exe` + `MaxCall-beta.exe`) من `/opt/maxcall-updates` — التفاصيل في `UPDATE_REFERENCE.md` |
| التشغيل | خدمة `maxcall-dashboard.service`: **gunicorn بمستخدم `maxcall`** (غير root) — `ExecStart=/usr/bin/python3 -m gunicorn app:app -b 0.0.0.0:3001 --workers 2` + `EnvironmentFile=/etc/maxcall.env` — الأمر: `systemctl restart maxcall-dashboard` |
| الاعتماديات | `requirements.txt`: `Flask==3.0.3`, `pymysql==1.1.1`, `flask-cors==4.0.0`, `netifaces==0.11.0` + حزمة النظام `python3-gunicorn` |
| الاتصال بالقاعدة | مستخدم `maxcall@localhost` عبر `127.0.0.1:3306` (بيانات الاعتماد في `/etc/maxcall.env` بصلاحيات 640 لمجموعة `maxcall` — لا أسرار في الكود) |
| أوامر Asterisk | عبر `/usr/local/bin/maxcall-asterisk` (قائمة سماح صارمة) + sudo بلا كلمة مرور للمستخدم `maxcall` فقط — كل استدعاءات `asterisk -rx` المباشرة أُزيلت من الكود |
| الأمان (2026-09-06) | كل الاستعلامات parameterized + كلمات مرور لوحة الداشبورد bcrypt (ترقية تلقائية عند الدخول) + الجلسة لا تخزن كلمة المرور + `HttpOnly/SameSite` + حد معدل على الدخول + `/api/recordings` محصور داخل مجلد التسجيلات + AMI محصور على `127.0.0.1` ومنفذ `5038` مغلق خارجياً |

### نقاط النهاية (Endpoints)

| المسار | صلاحية | الوصف |
|---|---|---|
| `POST /api/agent/login` | عامة | دخول الموظف: يطابق `ombutel` (الاسم **أو** الامتداد + secret الجهاز)، ثم بديل `maxcall_dashboard.users`. يرد بـ `extension/name/sip_server` |
| `GET /api/stats` | جلسة | إحصائيات اللوحة (يرد ببيانات تجريبية ثابتة إذا لا مكالمات اليوم) |
| `GET /api/cdr` | جلسة | سجل CDR مع بحث/فلترة/ترقيم (`q/disposition/ext/page`) + اسم المتصل + التسجيلات + `online_names` لصفوف الفائتة |
| `GET /api/agents` | جلسة | الامتدادات + الحالة الحية (PJSIP contacts) + إحصاء اليوم |
| `GET /api/agents/<ext>` | جلسة | تفصيل موظف + أسبوع + آخر 20 + حالة الإيقاف + **فائتة أثناء توفره آخر 30 يوماً** (`missed_online`) |
| `POST /api/agents` | جلسة | إضافة موظف: الاسم + **كلمة مرور تُختار عند الإنشاء** (أو توليد عشوائي يُرد لمرة واحدة) — ينشئ صف الامتداد + صف الجهاز `ombu_devices` (بدونه لا دخول) + endpoint في `pjsip__50-1-extensions.conf` + **عضو طابور تلقائياً** + reload — مع تحقق لاحق من الحفظ |
| `POST /api/agents/<ext>/pause` | جلسة | إيقاف/استئناف عضو الطابور `{paused:true/false}` — مع تحقق من الحالة |
| `GET /api/reports?days=7` | جلسة | تقارير SLA للإدارة: مستوى الخدمة (رد ≤20s) + ASA + AHT + تخلي حسب اليوم + توزيع اليوم الحالي بالساعة + أداء الموظفين (مكالمات العملاء فقط، الداخلي مستبعد) |
| `POST /api/agents/<ext>/reset` | جلسة | تصفير كلمة مرور امتداد |
| `GET /api/recordings` + `/api/recordings/<f>` | جلسة | قائمة وتشغيل التسجيلات من `/var/spool/asterisk/monitor` |
| `GET /api/callers/<phone>` | عامة | بطاقة المتصل (يستخدمها MaxCall) |
| `GET /api/calls/history?agent=EXT&limit=50` | عامة | سجل الكول سنتر **المشترك** (نفس القائمة لكل الموظفين حتى غير المسجلين وقتها): يستبعد الداخلي ext-to-ext كلياً، يجمّع مكالمات العميل حسب `linkedid`، يكشف المجيب من `channel`، والفائت يُمسح عمومياً بأول معاودة من أي موظف |
| `POST /api/callers` | عامة | حفظ بيانات المتصل (تحديث جزئي: تُكتب الحقول المرسلة فقط): `name/notes/file_no/phone2/relation` (upsert) |
| `GET /api/callers/<phone>/history` | عامة | سجل رقم واحد الكامل عبر كل الموظفين والأيام (لبطاقة المتصل): `{history:[{time,status,duration,answered_by,has_rec}]}` |
| `GET /api/missed?from=&to=&agent=&limit=` | جلسة | تقرير الفائتة الكامل (حتى 93 يوماً + فلتر موظف أثناء توفره + خيارات جاهزة في الداشبورد) — كل عنصر: `date/time/number/name/online/online_names` |
| `GET /api/callers?q=&limit=` | جلسة | دليل المتصلين للداشبورد |
| `POST /api/callback {agent,number}` | جلسة | معاودة: يرن الموظف أولاً ثم يوصله بالزبون (`originate`) — موجودة API لكن مخفية من الداشبورد حالياً |
| `PUT /api/agents/<ext> {name}` | جلسة | تغيير اسم المستخدم للموظف |
| `GET /api/agents/<ext>/export` | جلسة | ملف الموظف الكامل CSV (شهر + 200 مكالمة + تسجيلات) |
| `GET /api/callers/export?q=` | جلسة | ملف المتصلين الكامل CSV |
| `GET /maxcall-ip` | عامة | عناوين السيرفر ومنافذه |
| `GET /api/his/caller/<phone>` | جلسة أو Basic auth (نفس بيانات `/api/agent/login`) | بطاقة مريض من Solver WebHIS لنافذة المتصل (قراءة فقط) عبر `his_broker.py` — تفصيل كامل في §2.1 أدناه |
| `GET /api/insurer/health` | LAN فقط (`ipaddress.is_private`) | حالة وعمال الوساطة — بلا دخول موظف |
| `POST /api/insurer/job` | LAN فقط | `insurer` + `member` (+`card`) → طابور الوساطة على السيرفر — يفتح تبويب تأمين معزول ولا تبويب عند الموظف؛ يرد بـ `jobId` |
| `GET /api/insurer/job/<id>` | LAN فقط | حالة المهمة + خطوات التقدّم؛ `?pdf=1` يعيد PDF مضمّناً (base64) عند الاكتمال |
| `POST /api/insurer/job/<id>/answer` | LAN فقط | إجابة كابتشا مرتبطة برمز عقد إيجار 15 ثانية (`token`) — إجابة أولى تُقبل، والبقية تُرفض برسالة تنقل |

### 2.1 تكامل WebHIS — `his_broker.py` (منشور 2026-10-19)

وساطة قراءة فقط بين نافذة caller details في تطبيق MaxCall وسجل المريض في Solver WebHIS
على `192.168.1.77:81` (اسمه المحلي `solverserver`). تخدم نقطة `GET /api/his/caller/<phone>`:
بطاقة مريض مضغوطة (العمر/الجنس، الفئة/التأمين، المبيت، المديونية، الموعد القادم،
آخر 3 زيارات، آخر فاتورة صافية)، وقائمة اقتراحات مرضى عند أن الرقم مسجل على أكثر من سجل،
مع `source_url` للارجاع.

| البند | القيمة |
|---|---|
| المسار | `/opt/maxcall-dashboard/his_broker.py` (المصدر المعتمد `server/his_broker.py`) |
| عمليات WebHIS المسموحة | أربع عمليات مرمَّزة في الكود (بحث/بطاقة/زيارات/فواتير — كلها قراءة) + `POST UserInfo/ValidateUser` للتوكين (JWT ~100 يوم) — لا بروكسي عام ولا أي عملية كتابة |
| التوكين | بالذاكرة فقط (لا ملفات ولا سجلات) + تجديد تلقائي عند بقاء < 65 يوماً + إعادة دخول واحدة عند 401/403 + كاش نتائج 10 دقائق (LRU حده 500) |
| متغيرات البيئة | `HIS_USERNAME`/`HIS_PASSWORD` إلزامية و`HIS_HOST`/`HIS_PORT` اختيارية في `/etc/maxcall.env` — غيابهما أو كلمة مرور خاطئة → `{"ok":false}` وتُخفى بطاقة المريض بهدوء دون إزعاج الموظف |
| التوثيق على المسار | جلسة الداشبورد أو Basic auth بنفس بيانات الموظف المتحقق منها في `/api/agent/login` (`_verify_agent_credentials`) — لا آلية دخول موازية |
| حد المعدل | 30 طلباً في الدقيقة لكل IP على مسارات HIS |
| ضمانات الخصوصية | لا كلمة مرور أو توكين في كود العميل أو الـ HTML المضمّن، ولا تسجيل لبيانات مرضى/توكين في سجلات السيرفر؛ بيانات المريض تبقى داخل شبكة MaxCall الداخلية |
| اختبارات القبول | `cd server && HIS_USERNAME=... HIS_PASSWORD=... python3 test_his_broker.py` — 57 اختباراً ضد WebHIS الحقيقي |
| تحصينات موصى بها (لم تُنفذ) | TLS أمام 3001 أو جدار يحصر المنفذ على شبكة MaxCall + حساب WebHIS مخصص read-only بدل حساب شخصي (`abdullah`) |

## 2.2 وسيط التأمين على السيرفر — `insurer_broker.py` + خدمة `maxcall-broker` (MedNet/NextCare 2026-11-04 صباحاً · Liva 2026-11-04 مساءً)

وساطة تشغيل لغوية Python Playwright تشغّل مواقع التأمين على السيرفر
(بلا نافذة على أجهزة الموظفين): MedNet + NextCare (2026-11-04 صباحاً) +
Liva عبر بوابة FortiGate + كابتشا موظف (2026-11-04 مساءً).

| البند | القيمة |
|---|---|
| الخدمة | خدمة `maxcall-broker.service` (user=root) — `Restart=on-failure` |
| مصدر الكود | `server/insurer_broker.py` (الخدمة) + `server/brokers/{base,mednet,nextcare,liva}.py` |
| التثبيت على السيرفر | `/opt/maxcall-broker/` (venv معزول: playwright + flask — خارج بايثون النظام) + بروفايل ثابت `/var/lib/maxcall-broker/profiles/<id>/` |
| المنفذ الداخلي | `127.0.0.1:3011` (لا يُقدم للموظفين — يقبله `app.py` فقط كوسيط LAN) |
| واجهة الموظف | المسار `/api/insurer/*` في `app.py` — **LAN فقط** (`ipaddress.is_private`) بلا تسجيل دخول لكل موظف — التثبيت فقط |
| نموذج العمل | عامل دائم لكل تأمين + طابور تسلسلي (الموظفون يتشاركون الجلسة)؛ إعادة تحميل الصفحة/النجا. |
| الحالة | **MedNet + NextCare منشورتان ومُختبرتان حياً** — كامل التدفق: دخول → استعلام → Print → PDF (MedNet ~6ث، NextCare ~9ث) · **Liva منشورة (0.9.21)** — VPN → بوابة المزوّد → **تتوقف عند كابتشا البوابة بانتظار موظف**؛ الباقي (صفحة العضوية → الطبيب → الهاتف → PDF) جاهز وغير مُعتمد حياً بعد |
| الطباعة (كيف نصل للـPDF) | اعترض `window.open` قبل الموقع نفسه → الطلب يُجلب مباشرة بكوكيز الجلسة (`pdfdisplayservlet` يُعيد `application/pdf`؛ |
| | `window.open` لا يفتح نافذة إطلاقاً في الـheadless — لا popup ولا viewer — إذن التدفق هشّ ويُقاس مباشرة |
| النبضة | كل 4 دقائق (في حال عدم جريان أي طلب حقيقي) — MedNet/NextCare: تحقق حقيقي بآخر رقم عضوية ناجح (يتخزّن `/var/lib/maxcall-broker/heartbeat.json`) · **Liva: تجديد VPN فقط** (بلا اقتراب من كابتشا بوابة المزوّد إطلاقاً — محاولة آلية بلا موظف تخاطر بقفل الحساب؛ وبحد أقصى دخول VPN واحد كل ساعة) |
| التحقق البشري | **كابتشا Liva** (نوع `captcha`): صورة تُلتقط بعنصر `#CaptchaImage` (JPEG) وتُمنح للجهاز الطلب بفترة **15 ث** تدوير لكل جهاز؛ الإجابة الأولى تُقبل والبقية تُرفض؛ الانتهاء بلا إجابة = لا شيء يُرسل (الرخصة تنتقل للجهاز التالي) · **اختيار طبيب** (نوع `choice` — عند عدم تطابق اسم طبيب HIS): قائمة أسماء تُعرض في HIS بفترة **75 ث**؛ النقر على عنصر = الإجابة · إجابة فارغة من الموظف = إلغاء المهمة |
| | ملاحظة: Liva تتطلب كابتشا عند كل دخول لبوابة المزوّد (غالباً أول طلب باليوم) — تُعرض على صفحة المشفى كما هو مخطط |
| بيانات الدخول | `/etc/maxcall-insurers.json` بصلاحيات 600، قابلة للتحديث في أي وقت؛ الوسيط يقرؤها عند كل محاولة دخول (بلا restart) |
| التنظيف | كل 6 ساعات: حذف `Cache*`/`Code Cache`/`GPUCache` + ذاكرة التخزين المؤقت للمتصفح — **لا تلتمس كوكيز الجلسة**؛ |
| | `MemoryMax=3G` في الوحدة |
| صورة الجلسة | ملفات session-confined فقط — لا تخرج للموظف؛ ابتقاء ثابت عبر الأيام بين الإشعالات الروتينية |

---

## 2.3 الوصول إلى سجلات المشفى (Solver WebHIS) — `solverserver`

> **هذا القسم هو "الدليل (quick access)" لأي وكيل أو مطور يتعامل مع بيانات المرضى.**
> لا تعيد اكتشافه — استخدم ما يلي directly.

### الاتصال (verified)

| البند | القيمة |
|---|---|
| المضيف | `solverserver` (اسم السيرفر في رسائل الخطأ: `Server: SOLVERSERVER`) — عنوانه حالياً `192.168.1.77` but **لا تستخدمه** (DHCP) |
| المنفذ | `81` (واجهة Angular + API معاً) |
| **قاعدة الـ API** | `http://solverserver:81/API/Api/` — **تنبيه:** بدون `/API/Api/` بادئة → 404 |
| تسجيل الدخول | `POST /API/Api/UserInfo/ValidateUser` بـ `{"Usr_Id":..., "Usr_Pwd":..., "ApiKey":"", "isPasswordEncrypted":false, "Origin":""}` → يعيد `AuthorizationToken` (JWT، ~100 يوم) |
| كل طلب | `POST /API/Api/<Controller>/<Method>` + body `{"loggedInInfo":{...}, "Payload":...}` + header `Authorization: Bearer <JWT>` |
| **فخ إلزامي** | `loggedInInfo` **يجب** أن يحتوي `"ApiKey":"MAX"` (أو أي non-empty). بدونه كل طلب يفشل بـ `Object reference not set to an instance of an object.` — ليس خطأ تشفير ولا صلاحيات |
| الـ token | JWT — `exp` قابل للقراءة محلياً (base64 للـpayload، لا تحقق توقيع). تجديد قبل 65 يوماً (مarge safety) |
| أزمنة الاستجابة | بحث بالهاتف: 1.1–1.8 ثانية. كاش 10 دقائق (LRU حده 500) في `his_broker.py` |
| الواجهة | `http://solverserver:81/#/home/hmis/op/PatientSearch` (يعمل) |

### أشكال الـPayload (verified — لا تخمّن)

| Endpoint | payload | ملاحظة |
|---|---|---|
| `PatientSearch/GetSearchResult` | **كائن** (جميع الحقول، `""` = لا فلتر) — `PhoneNumber` بحث جزئي | يعيد قائمة (رقم واحد قد يعود لعدة مرضى) |
| `patient/GetPatientByID` | `{"Pat_ID":"<id>"}` — **string** + exact key capital | ملف ~200 حقل (طبيب معالج، تأمين، مديونية، موعد) |
| `PatientSearch/GetPatientVisitHistory` | **RAW value** (Pat_ID integer/string) — ليس كائناً | `{"Pat_ID":...}` → SQL conversion error |
| `PatientSearch/GetPatientBillHistory` | `{"Visit_No":n, "Trans_Type":"BILL"}` | فواتير زيارة واحدة |
| `IPAdmission/GetAdmissionDetailByPatID` | `{"Pat_ID":n}` | `"No Current Admission found"` = حالة طبيعية، لا خطأ |

### لا تستخدم

- `patient/GetPatientFileReportByID` — يخلط `Pat_ID` برقم ملف داخلي → يعيد مريضاً مختلفاً.
- `PHR_API/Api/*` — غير منشور (404).
- منفذ 83 (النظام القديم WebForms) — خارج النطاق.

### فحص `solverserver` (للتأكد من الوصول)

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://solverserver:81/   # 200
curl -sS --max-time 10 -X POST http://solverserver:81/API/Api/Utility/GetApiVersion   -H "Content-Type: application/json" -d '{}'   # "2.1.50303.1800"
```

### مرجع code

- `his-integration/his_client.py` — مكتبة Python قراءة فقط، مختبَرة (لكن `.gitignore`d — لا يُنسخ مع repo).
- `his-integration/EXECUTION_PROMPT.md` — 512 سطر، كل تفصيل (§1.1–§2.7).
- `his-integration/webhis_endpoints.txt` — خريطة 1300+ endpoint / 298 controller.
- `server/his_broker.py` — الوسيط (القراءة فقط) — مرجع "كيفية التكامل".

---

## 3. قواعد البيانات (MariaDB 10.11 — `mariadb.service`)

| القاعدة | الدور | أهم الجداول |
|---|---|---|
| `maxcall_dashboard` | بيانات اللوحة والمتصلين | `callers(phone PK, name, notes, file_no, phone2, relation, total_calls, last_call)`؛ `users(id, username, password bcrypt, role, display_name)`؛ `ring_presence`؛ `presence_log` |
| `asterisk` | CDR وCEL وسجل الـ Queues | `cdr` (`calldate/src/dst/dcontext/disposition/billsec/clid/recfile/uniqueid`)؛ `cel`؛ `queues_log` (تعيين ODBC عبر `extconfig__20-queues-log.conf`) |
| `ombutel` | امتدادات VitalPBX | `ombu_extensions(extension, name, ...)` + `ombu_devices(secret)` — مصدر الحقيقة للدخول |
| `provisioning`, `vitalpbx_logs` | نظام VitalPBX | لا تُمس |

### ملاحظات البيانات الحالية
- حركة خارجية حقيقية موجودة (أرقام دولية ومحلية طويلة) بجانب مكالمات الاختبار (`77181992` من `777` و `93744242` من `778`) والداخلية (`101↔103` بسياق `cos-all`).
- منذ 2026-09-21: أسبوع **2026-09-15→21** معبأ بمكالمات منسوخة من أسبوع النشاط الأقصى (309 مكالمة) بأدوات `/root/maxcall-datafill/` (للتراجع: `python3 undo_fill.py --force`).
- الرقم الخارجي يُميَّز بطوله (>4 خانات) مقابل الامتدادات ثلاثية الخانات.
- `disposition` المرصودة خارجياً: `ANSWERED/BUSY/CONGESTION/FAILED/NO ANSWER` — كافية لحساب حالات المعاودة.
- `queues_log.time` نصي + عمود `created` زمني فعلي — استخدم `created` للترتيب.

## 4. التسجيلات والشبكة والنسخ

| البند | القيمة |
|---|---|
| التسجيلات | `/var/spool/asterisk/monitor` (تسجيلات المكالمات `MixMonitor` بصيغة `YYYY/MM/DD/...wav`) + `/var/spool/asterisk/voicemail` — تُعرض عبر `/api/recordings` (محصورة داخل المجلد، لا مسارات مطلقة) |
| المقاطع الصوتية المخصصة | `/var/lib/asterisk/sounds/custom/`: `WelcomeMaxcall3cx.wav` (الترحيب) + `moh/MaxcallAD_8k_mono3cx.wav` (أثناء الانتظار) |
| المنافذ المفتوحة | `5060/udp` (+`5060/tcp` للاحتياط) SIP، `3001` API، `80/443` VitalPBX، `3306` محلية فقط — منفذ `5038` (AMI) **مغلق خارجياً منذ 2026-09-06** |
| روابط الوصول | الكل بالاسم `maxcall.local`: VitalPBX Admin `https://maxcall.local` — اللوحة `http://maxcall.local:3001` — الـ API `http://maxcall.local:3001/api/...` — التسجيل SIP `maxcall.local:5060` — مرآة التحديث `http://maxcall.local:3001/updates` |
| DNS | الاسم `maxcall.local` يُعلن عبر avahi من السيرفر — إن لم يتحلل على جهاز، أضف سطر hosts بالعنوان **الحالي** لذلك الجهاز (يُقرأ من السيرفر بـ`hostname -I`) ولا تُثبّت رقماً في أي إعداد برمجي |
| النسخ الاحتياطي | محلي: كرون يومي `03:30` (`/usr/local/bin/maxcall-backup` → `/var/backups/maxcall/`، احتفاظ 7 أيام) — خارجي: ريبو `hxg3/maxcall-backup` (قواعد+إعدادات+تطبيق، مشفر age، 30 يوماً، دفع 03:45) + ريبو `hxg3/maxcall-recordings` (تسجيلات، نافذة 30 يوماً + حارس 4GB + squash شهري، دفع 04:15) — مفاتيح الدفع deploy-keys للسيرفر + مفتاح الفك خارج السيرفر — تجارب استعادة ناجحة (محلية ومن GitHub) |

## 5. علاقة هذا الريبو بالسيرفر

| المكوّن المحلي | المقابل على السيرفر |
|---|---|
| `maxcall/` (تطبيق الموظف) | يسجّل SIP على `maxcall.local:5060` + دخول/متصلون عبر `:3001/api/agent/login` و `/api/callers` — بطاقة المتصل تدعم `file_no/phone2/relation` منذ 2026-09-06 (تتطلب exe جديد من CI؛ النسخة الحالية تعمل دون مسح الحقول الجديدة بفضل التحديث الجزئي) |
| `Dashboard/dashboard.html` (لوحة المشرف — المصدر المعتمد) | تُنشر إلى `/opt/maxcall-dashboard/templates/dashboard.html` (بدون restart — ملف ثابت) — إنجليزية + دارك مود + هوية MaxCall — الأقسام: Overview / Calls (توفر الموظفين تحت حالة الفائتة) / Missed (تقرير كامل بفترات + فلتر موظف) / Agents (ملف كامل + تسجيلاته + **فائتة أثناء توفره** + rename + export + كلمة مرور عند الإنشاء + **إيقاف مؤقت Pause/Resume**) / Recordings / Callers (ملف المتصل يعرض التوفر للفائتة + رقم الملف/رقم بديل/صلة المتصل + بحث برقم الملف + export) / **Reports (SLA يومي + اليوم بالساعة + أداء الموظفين)** — **بلا تحديث تلقائي** (أزرار Refresh يدوية فقط منذ 2026-09-06) |
| `server/` (مصدر السيرفر المعتمد — جديد 2026-09-06) | `app.py` + `presence_cron.py` + `extensions__99-maxcall.conf` + `queues__99-maxcall.conf` + `maxcall-asterisk.sh` + `maxcall-backup.sh` — النشر في `server/README.md` |
| `scripts/` | أوامر التشغيل/إعادة التحميل (asterisk reload, restart dashboard) |

## 6. مخاطر مرصودة (تُعالج حسب الأولوية)

1. ~~**SQL بالدمج النصي**~~ — **أُصلح 2026-09-06**: كل الاستعلامات parameterized.
2. ~~**أسرار مضمّنة**~~ — **أُصلح 2026-09-06**: `/etc/maxcall.env` (600) + كلمات اللوحة bcrypt.
3. ~~**كلمة مرور افتراضية موحدة**~~ — **أُصلح 2026-09-06**: كلمة تُختار عند الإنشاء أو توليد عشوائي + إنشاء صف الجهاز تلقائياً (كان مفقوداً — الموظف الجديد لم يكن يدخل أبداً) + صلاحيات `INSERT/DELETE` لمستخدم القاعدة.
4. **دخول الموظف يقبل الامتداد أو الاسم** — عميل MaxCall يفرض username (تحقق مزدوج)، والتوحيد الكامل يتطلب قراراً إدارياً.
5. ~~**لا نسخ ملفات خارجي**~~ — **تحسّن 2026-09-06**: نسخ في `/root/maxcall-backup-20260906/` + تجربة استعادة ناجحة (تبقى نسخة خارج السيرفر مستقبلاً).
6. `/api/stats` يرد أصفاراً صريحة عند غياب مكالمات اليوم — مقبول للعرض، لا يُبنى عليه تقارير.
7. نقاط MaxCall (`/api/calls/history` و `/api/callers*`) بلا جلسة (لتوافق التطبيق) — محمية بالتحقق من المدخلات وحد المعدل؛ التوكن الكامل يتطلب نسخة exe جديدة.
8. ملفات `.bak*` و `extensions__99-maxcare.conf.bak-rec` أسماء تاريخية — تُترك كأرشيف.

## 7. سجل التغييرات (يُحدَّث مع كل مهمة)

| التاريخ | الوصف |
|---|---|
| 2026-10-19 | إطلاق تكامل WebHIS بخدمة `his_broker.py` + نقطة `/api/his/caller/<phone>` (قراءة فقط، توثيق جلسة/Basic بنفس بيانات الموظف، توكين في الذاكرة مع تجديد تلقائي، كاش 10 دقائق، حد معدل) — منشورة ومُثبَّتة على السيرفر مع 57 اختبار قبول ناخلها ~0.85 ثانية لكل بطاقة عبر الكاش — بطاقة المريض تظهر في نافذة caller details المنبثقة |
| 2026-10-08 | تصحيح إقلاع نهائي: تحويل القرص إلى GPT + قسم ESP (1GB على `sdb3`) + تثبيت GRUB-EFI (`x86_64-efi`) على sda — السيرفر الآن يقلع الآن من القرص مباشرة عبر **UEFI** بدون USB تمام (تم التحقق: uptime نظيف، `/` على `/dev/sdb1`، `/dev/sda1` تسجيلات، sdc1 الفلاش غير مستعمل) — ملاحظة: أسماء sda/sdb تتقلب بين البطّارات (SATA) ومرتبطة حالياً: الجذر UUID `50a292a0…`، التسجيلات UUID `8541918b…` |
| 2026-10-08 | هجرة إلى سيرفر bare-metal i5/8GB (VitalPBX نفسه بكامل الإعدادات): نسخ خام `VitalPBX-MaxCare.vmdk → RAW` عبر USB، تصحيح `avahi-daemon.conf` (`allow-interfaces=enp1s0` بدل `enp0s3`)، الشبكو `enp1s0` عبر DHCP، ديسكات منفضلين: sda (223G ext4 → `/var/spool/asterisk/monitor` للتسجيلات عبر fstab بـ UUID) وsdb (238G، الجذر على `sdb1` 19G ext4 من الصورة) — Windows أُلغي — النظام يعمل الآن من القرص مباشرة (YYYY-10-08، uptime نظيف، asterisk/mariadb/maxcall-dashboard running، `maxcall.local` ينحو إلى `192.168.1.32`) |
| 2026-09-03 | إنشاء المرجع من جرد SSH شامل (أول توثيق) |
| 2026-09-03 | تصحيح: لا Queues — التدفق RingAll مخصص (`extensions__99-maxcare.conf` + `777/778` للاختبار) + Trunks حقيقية (Twilio + GSM) + روابط الوصول ومواقع التسجيلات والمقاطع |
| 2026-09-03 | إصلاح Twilio: تحديث origination للـ IP الحالي + credential/IP-ACL صالحة + endpoint السيرفر UDP/5060 + تحديث `external_*` + مسار اختبار `9` — مكالمة اختبار `no-answer` بلا أخطاء (الإشارة سليمة، بانتظار اختبار الصوت) |
| 2026-09-03 | تشخيص الحجب: إثبات أن SIP الوارد محجوب على الراوتر (فحص UDP خارجي لم يصل) — REST API يعمل (مكالمة 14s) لكن ساق SIP للداخل لم تصل — المطلوب port-forward في الراوتر قبل أي اختبار صوت |
| 2026-09-03 | سجل الكول سنتر: نقطة `/api/calls/history` جديدة في `app.py` (نسخة `.bak-history`) — تُقرأ من `cdr`/`linkedid`/`callers` |
| 2026-09-03 | سجل مشترك rev2: استبعاد الداخلي + تجميع العميل حسب `linkedid` + كشف المجيب من `channel` + مسح الفائت بالمعاودة عمومياً |
| 2026-09-03 | سجل المتصل: نقطة `/api/callers/<phone>/history` (آخر 30 لرقم واحد، كل الموظفين) |
| 2026-09-03 | الداشبورد الجديد: `/api/missed` + `/api/callers?q` + `/api/callback` (معاودة حقيقية عبر originate) + إزالة تكرار التسجيلات + أصفار صريحة + قالب إنجليزي بدارك مود |
| 2026-09-03 | داشبورد rev2: شعار حقيقي + MaxCall + بلا callback مرئي + بحث ومدة التسجيلات + ملفات CSV + إصلاح reset كلمة المرور (devices + كتلة auth) + تغيير الاسم |
| 2026-09-03 | داشبورد rev3: أسماء عرض إنجليزية + reset ذري مع تحقق قراءة + سجل الموظف باتجاهين + `/api/presence` للمتصلين الآن في الفائتة |
| 2026-09-03 | داشبورد rev4: دخول جديدة + ملف متصل (سجل + تسجيلات) + حضور تاريخي (`presence_log` كل دقيقتين + `online[]` لكل فائتة) + `rec` في سجل المتصل |
| 2026-09-03 | داشبورد rev5: نسب تسجيلات الموظف عبر `channel` (سطور RingAll تحمل `dst=s`) + reset بتشخيص لكل مخزن + مدة الملف في الجدول |
| 2026-09-03 | تسجيلات حقيقية فقط: `has_rec` يتطلب صوتاً فعلياً (≥2s) لا مجرد ملف — ملفات 44-byte الفارغة تُستبعد + تنظيف يومي لها |
| 2026-09-03 | حضور لحظة الرنين: `ring_presence` عبر `/api/ring/snapshot` من خطة `maxcare-in` (يستبدل التخمين) — `/api/missed` يعرض `online[]` لذلك الرنين بالذات |
| 2026-09-03 | أسماء في كل القوائم + سجل المتصل الكامل (500) مع ملخص + نسب المجيب للتسجيلات |
| 2026-09-03 | تسجيلات البروفايل تُفلتر للصوت الحقيقي + حركة خفيفة في الداشبورد (دخول/hover/عدّادات/shimmer) |
| 2026-09-03 | أسماء الموظفين: `stats.agents` بصيغة `{total,name}` و `online_names[]` في الفائتة |
| 2026-09-03 | عطل شامل: `fail2ban` حظر الأجهزة الثلاثة بعد محاولات الدخول — فُك الحظر + استثناء `192.168.1.0/24` و `192.168.56.0/24` دائم |
| 2026-09-03 | التسجيلات: `MixMonitor` كان يسجل دون كتابة `CDR(recfile)` — أُضيف `Set` في `extensions__99-maxcare.conf` + تعبئة 92 صفاً قديماً بمطابقة uniqueid |
| 2026-09-03 | صلاحيات القاعدة: `GRANT UPDATE` على `ombu_devices` و `ombu_extensions` لمستخدم `maxcall` — بدونه كانت الكتابة تُرفض بصمت (سبب فشل reset/rename) + تحقق قراءة في rename + توحيد سجل البروفايل |
| 2026-09-06 | أمن P0: كل SQL parameterized + أسرار `/etc/maxcall.env` + كلمات اللوحة bcrypt + جلسة مصغرة + حد معدل + `/api/recordings` محصور + AMI على `127.0.0.1` + إغلاق `5038` خارجياً + حذف `maxcare_dashboard.db`/`maxcare.db` الصفرية |
| 2026-09-06 | كلمات مرور عند الإنشاء: `POST /api/agents` يقبل `password` أو يولد عشوائياً (يُرد لمرة واحدة) + إنشاء صف `ombu_devices` تلقائياً + `GRANT INSERT,DELETE` (الإضافة لم تعمل أبداً بصمت) + تحقق لاحق من الحفظ + واجهة الداشبورد (حقل + Generate) |
| 2026-09-06 | حذف Twilio نهائياً: حذف trunk DB + كتل pjsip + سياقات `trk-1/*` + مسار `_9X.` + إعادة توجيه الخروج إلى `trk-2` (بوابة GSM) — أُبقي trunk `Virtual_GSM_Gateway` فقط |
| 2026-09-06 | توحيد التسمية maxcall: قاعدة `maxcall_dashboard` + مستخدم `maxcall` + `/opt/maxcall-dashboard` + خدمة `maxcall-dashboard` + سياقات `maxcall-in/maxcall-bridge` + صنف MOH `maxcall` + أصوات `WelcomeMaxcall3cx`/`MaxcallAD` + مضيف `maxcall` (`maxcall.local`) + مسار `/maxcall-ip` + كل كود MaxCall والوثائق — سجلات CDR القديمة تحتفظ بـ `maxcare-in/trk-1-in` تاريخياً |
| 2026-09-06 | بيانات المتصل الموسعة: أعمدة `company/email/category/vip` + API + داشبورد (جدول/ملف/تصدير CSV) + بطاقة MaxCall (HTML + C++ + عقد BRIDGE + `res/crm_popup.bin` مُعاد بناؤه — يتطلب exe جديد) |
| 2026-09-06 | تبسيط الداشبورد: إلغاء التحديث التلقائي (Refresh يدوي فقط) — بلا ساعات عمل (24/7 افتراضياً) — نسخ `/root/maxcall-backup-20260906/` + تجربة استعادة ناجحة + حذف قاعدة `maxcare_dashboard` القديمة |
| 2026-09-06 | إصلاح تعارض الفائتة: العداد كان يحسب كل صفوف CDR غير المردودة (بما فيها الداخلي 103←101) بينما الصفحة تعرض مكالمات العملاء فقط — وُحّد المنطق في `_compute_missed()` (مكالمات العملاء الواردة بلا رد) للعداد والصفحة معاً |
| 2026-09-06 | حقول المتصل المفيدة للمستشفى: استُبدلت `company/email/category/vip` بـ `file_no` (رقم الملف — مفتاح الربط المستقبلي مع نظام المستشفى) + `phone2` (رقم بديل للمعاودة) + `relation` (المريض/قريب/أخرى) — بحث برقم الملف + تحديث جزئي يحمي النسخ القديمة من المسح |
| 2026-09-06 | طابور حقيقي `maxcall-q` بدل RingAll اليدوي: دورات 25s + انتظار دائم + `ringinuse=no` + `wrapup 5s` + أعضاء تلقائيون عند الإضافة/التغيير + Pause/Resume في الداشبورد + سجل `queues_log` — اختبار حي (ENTERQUEUE/CONNECT/COMPLETE + تسجيل + CDR) بلا إزعاج الموظفين + كشف المجيب من `queues_log` كاحتياط |
| 2026-09-06 | تقارير SLA: نقطة `/api/reports` + تبويب Reports (خدمة ≤20s + ASA + AHT + تخلي يومي + اليوم بالساعة + أداء الموظفين — مكالمات العملاء فقط) |
| 2026-09-06 | نسخ مجدول: `/usr/local/bin/maxcall-backup` يومياً 03:30 (قواعد + تطبيق + إعدادات + تسجيلات، 7 أيام) + تجربة استعادة ناجحة |
| 2026-09-06 | تشغيل إنتاجي: gunicorn (2 workers) بمستخدم `maxcall` (غير root) + أوامر Asterisk عبر `maxcall-asterisk` بقائمة سماح + sudo مقيد + إعادة اختبار شامل لكل التكاملات |
| 2026-09-06 | باك اب GitHub خارجي: ريبو `hxg3/maxcall-backup` + ريبو `hxg3/maxcall-recordings` (خاصان، دفع يومي مشفر age، نافذة 30 يوماً، حارس 4GB، squash شهري) + deploy-keys + تجربة استعادة من GitHub ناجحة (تسجيل مطابق md5 + قاعدة 8 متصلين) |
| 2026-09-06 | الفائتة تقرير كامل: `/api/missed` بفترات حتى 93 يوماً + خيارات جاهزة (Today/7/30/90/Custom) + فلتر موظف (فائتة أثناء توفره) + حقل `date` + التوفر في Calls (`online_names`) وملف المتصل (`online`) وبروفايل الموظف (`missed_online` 30 يوماً) — المصدر: لقطة الرنين ثم `presence_log` ثم heuristic |
| 2026-09-06 | تبسيط الداشبورد: حذف كل أزرار Search/Refresh — بحث وفلاتر فورية (debounce) والتبويب يجلب بياناته عند فتحه — الرسم البياني بأسماء الأيام + tooltip (اليوم الكامل + العدد) + نسبة الرد تحت إجمالي اليوم + عناوين Reports بتوضيحات hover |
| 2026-09-21 | تعبئة أسبوع البيانات (2026-09-15→21): نسخ الأسبوع الأكثر نشاطاً (2026-08-28→09-03، 309 مكالمة) مع إزاحة الأيام الكاملة وفق أسبوعية اليوم (Tue→Tue +14d لـ 09-01…03، Fri/Mon→ +21d لـ 08-28…31) — تضمن: `cdr` (309 صف بجديد `uniqueid/linkedid` + `recfile` معاد توليده)، نسخ 28 ملف wav إلى مجلدات التواريخ الجديدة، `ring_presence` (مجموعة واحدة)، `presence_log` (3 سجلات)، تحديث `callers.total_calls/last_call` لـ `77181992`/`93744242` — تحقق شامل عبر الـ API (`/api/stats` أسبوع 30/14/90/36/67/61/13، `/api/recordings` 18 تسجيلاً قابلاً للتشغيل، `/api/missed`، `/api/reports`) + اختبار تراجع كامل (undo → إرجاع القيم الأصلية → إعادة التعبئة). الأدوات: `/root/maxcall-datafill/fill_week.py` + `undo_fill.py` + `manifest.json` (نشر 2026-09-21) — التراجع: `python3 undo_fill.py --force` |
| 2026-10-16 | **اعتماد الاسم بدل العنوان:** كل روابط الوصول في هذا المرجع أصبحت `maxcall.local` (الإدارة/اللوحة/API/SIP/مرآة التحديث) — العنوان الرقمي DHCP متغيّر ولا يُثبَّت في أي إعداد. مرآة التحديث في `const.h` صارت `http://maxcall.local:3001/updates`، والعميل يحوّل الاسم إلى IPv4 لحظة الطلب (تجنّباً لتعلّق WinInet على IPv6 link-local)، و`MIRROR_HOST` في `release.sh` صار `maxcall.local`. إن لم يتحلل الاسم على جهاز: `ssh-keyscan` للـSSH وسطر hosts بالعنوان الحالي |
| 2026-11-04 | **Liva عبر الوسيط (Phase 2) — 0.9.21:** `server/brokers/liva.py`: VPN FortiGate (`/remote/login`) بجلسة دائمة → بطاقة Provider (تُفتح window.open، تُلتقط وتُوجَّه داخل نفس التبويب) → بوابة المزوّد Angular → **كابتشا تُلتقط بعنصر `#CaptchaImage`** وتُعرض على شاشة الموظف عبر نفس قناة `state=captcha` (PayLoad type=captcha — 15 ث lease دوّار بين الأجهزة؛ انتهاء العداد = بلا إجابة تُرسل بدل إجهاض المهمة للجميع) → صفحة العضوية (`#txtMemberId` + `#btnSearch`) → الطبيب (jQuery UI — مطابقة HIS بلا حساسية لحالة الأحرف؛ فشل ⇒ **قائمة اختيار 75 ث** في HIS — `NeedsChoice` → نفس قناة الإجابة type=choice) → الهاتف `968-` (قناع mask + `setSelectionRange` + إدخال رقم-رقم) → انتظار امتلاء `#txtSpeciality` → طباعة `button.Printmember` → PDF يُلتقط بمعترض window.open (`\.pdf|pdfdisplayservlet|ReportsViewer`). النبضة لـ Liva = تجديد VPN فقط (لا تلمس كابتشا البوابة). تم نشر الوسيط واختباره حياً حتى نقطة الكابتشا (~8 ث من الطلب حتى ظهورها)؛ إتمام الطباعة يتطلب موظفاً يكتب الرمز |
| 2026-11-04 | نشر 0.9.20/0.9.19 (MedNet+NextCare): استرجاع إعادة تحميل صفحة الاستعلام fresh لكل طلب + نشر PDF فقط بعد مطابقة عارض حقيقي + 4 جولات متتالية ناجحة لـ NextCare حياً + تنظيف مرتقيات التقاط التقرير (`.ashx` العامة مرفوضة) |
| 2026-11-04 | الإضافة تلتقط طلبات التأمين للوسيط وتعرض تقدماً على صفحة HIS وتطبع PDF داخل الصفحة بلا نوافذ؛ الإزالة التامة لأي fallback محلي؛ `/api/insurer/*` محصور على LAN (`ipaddress.is_private`) |
| 2026-10-16 | توثيق مسار مرآة التحديث `GET /updates/<name>` (عام بلا مصادقة، ٦ أسماء مسموحة من `/opt/maxcall-updates`) في جدول هذا القسم + إنشاء `UPDATE_REFERENCE.md` كمرجع كامل لميزة التحديث الذاتي |
