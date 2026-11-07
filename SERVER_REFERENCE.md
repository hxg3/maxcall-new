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
| `POST /api/insurer/job/<id>/answer` | LAN فقط | إجابة كابتشا مرتبطة برمز عقد إيجار 90 ثانية (`token`) — إجابة أولى تُقبل، والبقية تُرفض برسالة تنقل |

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

## 2.2 وسيط التأمين على السيرفر — `insurer_broker.py` + خدمة `maxcall-broker` (MedNet/NextCare 2026-11-04 صباحاً · Liva 2026-11-05 مساءً)

وساطة تشغيل لغوية Python Playwright تشغّل مواقع التأمين على السيرفر
(بلا نافذة على أجهزة الموظفين): MedNet + NextCare (2026-11-04 صباحاً) +
Liva عبر بوابة FortiGate بحل كابتشا تلقائي (ddddocr) — ناضجة ومُعتمدة حياً (2026-11-05 مساءً).

| البند | القيمة |
|---|---|
| الخدمة | خدمة `maxcall-broker.service` (user=root) — `Restart=on-failure` |
| مصدر الكود | `server/insurer_broker.py` (الخدمة) + `server/brokers/{base,mednet,nextcare,liva}.py` |
| التثبيت على السيرفر | `/opt/maxcall-broker/` (venv معزول: playwright + flask + **ddddocr** — خارج بايثون النظام؛ `apt install tesseract-ocr` اختياري) + بروفايل ثابت `/var/lib/maxcall-broker/profiles/<id>/` |
| المنفذ الداخلي | `127.0.0.1:3011` (لا يُقدم للموظفين — يقبله `app.py` فقط كوسيط LAN) |
| واجهة الموظف | المسار `/api/insurer/*` في `app.py` — **LAN فقط** (`ipaddress.is_private`) بلا تسجيل دخول لكل موظف — التثبيت فقط |
| نموذج العمل | عامل دائم لكل تأمين + طابور تسلسلي (الموظفون يتشاركون الجلسة)؛ إعادة تحميل الصفحة/النجا. |
| الحالة | **MedNet + NextCare منشورتان ومُختبرتان حياً** — كامل التدفق: دخول → استعلام → Print → PDF (MedNet ~6ث، NextCare ~9ث) · **Liva: ✅ كامل الدورة منشورة ومُعتمدة حياً (2026-11-05 مساءً)** — VPN → بوابة المزوّد → **كابتشا تُحل تلقائياً (ddddocr) دون الموظف** → صفحة العضوية → الطبيب → الهاتف → doAjax (سبق الطباعة اليوم ⇒ **Reprint تلقائي** — نافذة `#MemberPop` → جدول `#PreAprovals` → اختيار صف الطبيب الأحدث → `Reports/FormDetail` → `window.open` → PDF) — مُلاحظ: الـPDF يُجلب بنفس جلسة VPN (HTTP 200، `application/pdf`) · **الجاهزية الدائمة (2026-11-05 ليلاً):** كل تأمين يستعيد نفسه تلقائياً بعد أي موت — مُثبت حياً على الثلاثة (MedNet 4.5 ث · NextCare 4.8 ث · Liva 14.1 ث من الموت إلى الجاهزية) |
| الطباعة (كيف نصل للـPDF) | اعترض `window.open` قبل الموقع نفسه → الطلب يُجلب مباشرة بكوكيز الجلسة (`pdfdisplayservlet` يُعيد `application/pdf`؛ |
| | `window.open` لا يفتح نافذة إطلاقاً في الـheadless — لا popup ولا viewer — إذن التدفق هشّ ويُقاس مباشرة |
| النبضة | كل ~4.5 دقيقة (في حال عدم جريان أي طلب حقيقي) — **استعادة كاملة تلقائية، وليست "تحقق فقط"**: MedNet: إعادة دخول + الوصول لشاشة العضوية (ملاحة ← موسّع ← **بناء بارد** — متصفح من الصفر، نفس مسار "صباح جديد" — يمحو أي حالة عالقة)؛ NextCare: إعادة تحميل صفحة الاستعلام (الدخول تلقائي إن انتهت الجلسة)؛ **Liva: بوابة FortiGate تُعيد البوابة تلقائياً بحل كابتشا (ddddocr) حتى 6 محاولات + تحقق URL `/Dashboard/`** — حد أقصى 10 استعادة بوابة/ساعة وفاصل 4 دقائق (لا عاصفة كابتشا) + بناء بارد بحد أقصى 4/ساعة. الحالة تُقرأ دائماً من الـDOM (idempotent)؛ الاستعادة تُسجَّل في `session-events.jsonl` |
| التتبع | **`/var/lib/maxcall-broker/session-events.jsonl`** — سطر JSONL لكل تبدل حالة: `dead` (بالتفاصيل واللحظة والرابط) · `restored` (+ `dead_for_s`) · `ready` · `restore_cold` · `lock` / `unlock` · `restore_failed` · `lock` — **كل جلسة تستعيد نفسها يظهر زوج dead←restored بالتوقيت الدقيق** — أداة `/opt/maxcall-broker/tools/broker_session_stats.py` تطبع ملخصاً (مرات الموت/الاستعادة، متوسط عمر الجلسة الفعلي، آخر الأحداث) |
| الكابح (Liva فقط) | **يرفض FortiGate الدخول برسالة `err=sslvpn_login_permission_denied` أو "Too many bad login attempts" (كلمة المرور منتهية/تغيرت) ⇒ توقف تام لكل محاولات Liva — بلا أي نبضة، بلا أي طلب، بلا الـcaptcha — حتى تتغير كلمة المرور في `/etc/maxcall-insurers.json` فيُفتح القفل تلقائياً ويستعيد النظام الجاهزية فوراً** · **MedNet/NextCare ليس لهما علاقة بالقفل إطلاقاً — لا تتكقف أي عملية لهما في أي ظرف** (قفل MedNet/NextCare لو وُجد لن يفيد — ليست بوابة FortiGate ولا تُتصفح بكلمة مرور خاطئة بشكل متكرر بأمان الوسيط) — عند قفل Liva تتوقف مهام ليفا فقط ويكمل MedNet/NextCare العمل عادياً · `/health` يُظهر `locked` + `lockReason` لكل تأمين |
| التحقق البشري | **كابتشا Liva تُحل تلقائياً (ddddocr)** — المكتبة مُثبتة في الـvenv (`/opt/maxcall-broker/venv`)، أوفلاين، لا "كوستا" — كل موظف يكاد لا يراها. آلية: البوابة تُصدر كابتشا جديدة عند كل رفض ⇒ حتى 6 محاولات تُقرأ وتُرسل من جديد؛ النجاح يُتحقق منه بوصول **URL لوحة المزوّد** (`/Dashboard/`) — إخفاء `#CaptchaInputText` غير كافٍ (يختفي أثناء إعادة البناء بعد الرفض!). عند فشل 6 محاولات ⇒ يُطلب من الموظف كباقي التحقق (fallback آمن) · الصورة تُعرض على شاشة الموظف عبر نفس قناة `state=captcha` (مهلة **600 ث**، lease تدوير 30 ث بين الأجهزة) · **اختيار طبيب** (نوع `choice`): قائمة أسماء تُعرض في HIS بمهلة **75 ث** |
| | ملاحظة تصميمية: شاشة الكابتشا تُجمّد دوران الصورة (`DefaultCaptcha/Refresh` مُجهض عبر `ctx.route`) — صورة ثابتة وصالحة طوال المهلة، فلا "انتهت الكابتشا" للموظف أو للحل الآلي |
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
| 2026-11-06 | **Liva — فلترة قائمة الأطباء + زر الطباعة كمسار افتراضي:** (1) **قائمة الأطباء كانت متسخة**: `JS_DOCTOR_ITEMS` يقرأ `ul.ui-autocomplete li.ui-menu-item` من **كل** قوائم jQuery على الصفحة — jQuery UI يبقيها كلها في الـDOM بعد إخفائها ⇒ عناصر بحث سابق (\"ELSAYED MUSTAFA\" مثلاً أو عناصر speciality) تتسرب للمطابقة ⇒ EKLEEL يقترح Elsayed Mustafa بدل EKLEEL · **الإصلاح**: `autocomplete('widget')` على `#txtDoctor` حصراً + التحقق من ظهور القائمة فعلياً (`is(':visible')`) + مسح القائمة قبل كل كتابة + النقر أيضاً مقصور على نفس القائمة وإلا `doctor_pick_failed` · (2) **الطباعة صارت عبر الزر الحقيقي `Print Online Claim Form`** — نفس معالج البوابة (validation ← PatientInfo) والتقاط `window.open(FilePath)` بنفس آلية Reprint · عند النجاح: PDF مباشرة · **عند الرفض تُقرأ رسالة `#FailedMsg` حرفياً** — وفقط رسالة \"Claim Form already printed\" تنتقل لمسار Reprint؛ أي رفض آخر (مثلاً عضو غير نشط / بيانات ناقصة) يتوقف برسالة السبب نفسه كما في البوابة — **لا تُسأل Reprint لأي فشل غير \"already printed\"** · (3) مثبت حياً على المريض الذي أُبلغ عنه: \`Dr.Ekleel Ali Adam Saad\` ⇒ `doctor=EKLEEL ALI ADAM SAAD` · نقر الزر ⇒ البوابة رفضت (مُطبوع اليوم) ⇒ Reprint ⇒ مطالبة \`0845-2026-00034781\` ⇒ PDF 222812 بايت · جسر مزدوج استُبدل: استدعاء doAjax المباشر القديم أُزيل كلياً |
| 2026-11-06 | **MedNet — الجاهزية منذ الإقلاع + إنقاذ المسار المتجمد/الميت:** (1) `keepalive_without_member = True` — النبضة تعمل منذ الإقلاع حتى قبل أول مهمة حقيقية (كان MedNet الوحيد المعطَّل قبل heartbeat.json) · (2) **فحص `JS_IS_LOGIN` داخل حلقة الاستعادة** — صفحة ميتة تُعرض كـ login page رغم بقاء الـURL mednetapprovals.com كانت تُحرق 30 ث (polling) + 15 ث (موسّع) + ربما بناء بارد (~70 ث)؛ الآن يُكتشف خلال أقل من ثانية وتُعيد الدخول مباشرة (~2-4 ث) · (3) **صفحة متجمدة تماماً** (ليست login، لا حقل بحث، لا رابط) — إعادة تحميل واحدة إجبارية عبر `page.goto(creds.url)` ثم تُستأنف الحلقة (الحالة كان مصيرها 45 ث+ ضائعة) · (4) مثبت حياً بعد إعادة التشغيل: الثلاثة warm خلال ~10 ث؛ نبضة MedNet ناجحة (eligibility_no=52619564، verdict=ok، attempt=1) |
| 2026-11-06 | **NextCare — التسخين المسبق للصفحة بعد كل مهمة:** (1) بعد نجاح الطباعة (doc) وبعد اكتمال keepalive، الوسيط يُعيد تحميل `EligibilityChecking2.aspx` ويتركها بكارت فارغ + Type جاهزة **قبل الإرجاع** — كلفة الـgoto (قياساً حياً: ~2 ث ثم ~6 ث لتقلب بوابة NextCare) تُدفع بعد استجابة الموظف لا قبل الطلب القادم · (2) الطلب التالي يجد `_page_is_fresh_eligibility()` صادقة ⇒ يتخطى goto كلياً — **تحقق حي مرتين متتاليتين بعد النشر**: `eligibility already fresh — reuse` ⇒ اكتمال خلال ~2-3 ث ثم `navigate ok eligibility pre-warmed for next request` · (3) فشل التسخين يظل محصوراً في try/except — لا يكسر نتيجة مكتملة، والطلب التالي يعود لمسار goto كما قبل · (4) الـ`remaining(kind) < 8` يتجاهل التسخين عند ضيق الهامش القانوني للمهمة · تاغ التراجع: `release/2026-11-06-pre-warm-redesign` |
| 2026-11-06 | **GIG — إصلاحان بعد أول تجربة حقيقية:** (1) **حقول Vipul لم تعد تظهر لـGIG** — كتلة `excelFields` كانت تُفعَّل بأي `kind==="html-form"` (vipul's "Fields to complete: Policy Number/Period of Treatment/Name of the Insurer")؛ أُُقيَت بـ`id==="vipul"` فقط · GIG يسقط لفرع "أنواع زيارة من الـpopup" تلقائياً (قائمة Outpatient/Inpatient/Emergency/Maternity/Dental/Optical مع الافتراضي Outpatient) · (2) **القالب أُعيد بناؤه ليطابق الطباعة الأصلية بالكامل** — صفحة A4 واحدة كثيفة (نفس التسلسل: Administrative Section ← Medical Section ← Declarations & Signatures side-by-side ← WARNING أحمر ← نص GIG القانوني) مع ترويسة GIG الأصلية (شعار يسار + عنوان رمادي يمين + طابع زمني) ودون أي عناصر زائدة · صفحة واحدة مثبتة بالقياس (النسخة القديمة كانت تفيض لـ2 صفحة) · الإصدار v0.10.1 — /ext/version.json مُحدَّث عبر `--deploy` · القالب المخدوم من المرآة مطابق حرفي للنسخة في `Extension/forms/GIG-form.html` (md5 af6718ed) |
| 2026-11-06 | **انضمام تأمينَي GIG وOQIC — نموذجان محلّيان بلا بوابة ولا وسيط ولا بيانات دخول:** (1) **GIG (AXA Gulf) — Direct Billing**: نموذج GIG الرسمي على giggulf.ae عامّ بلا تسجيل دخول وطباعته `window.print()` فقط — لهذا أُعيد بناء النموذج 1:1 محلياً كقالب `Extension/forms/GIG-form.html` (نفس النصوص القانونية والتوقيعات والشعار) ويُخدَم من المرآة `/ext/forms/GIG-form.html`؛ الامتداد يملأ تلقائياً من بطاقة HIS: `policy_no = card_no` بدون آخر 3 أجزاء (`65/XC/9600378/1/3/E/0` ← policy `65/XC/9600378/1` + membership `3/E/0`) · الاسم بنفس تنظيف فيبل · Gender من HIS · نوع الزيارة من قائمة الـpopup المنسدلة (Outpatient/Inpatient/Emergency/Maternity/Dental/Optical — الافتراضي **Outpatient** مثل NextCare) · صفر حقول إكسل وصفر مطالبات يدوية · (2) **OQIC (OMAN QATAR INSURANCE) — Direct Billing**: النموذج الرسمي PDF (34,738 بايت، صفحة واحدة) طُبع كما هو داخل تبويب HIS — طباعة القالب الثابت بلا أي تعبئة إطلاقاً · رُفع للمرآة `/ext/forms/OQIC-Claim-Form.pdf` · (3) الامتداد **v0.10.0**: مدخلا `gig` (html-form) و`oqic` (`static-pdf`) في `insurers.js` · معالج `insurer:printPdf` في background.js (نفس آلية الطباعة داخل تبويب HIS) · `insurer:printHtml` يعاد استخدامها لـGIG · his-reader تعرفت GIG/OQIC بالأسماء · زر الطباعة يعمل Offline تقريباً (جلب من المرآة المحلية فقط) · (4) نُشر v0.10.0 عبر `build-update.py --deploy` — `/ext/version.json` = 0.10.0؛ أجهزة HIS ستعرض زر Update في الـpopup · (5) الصفحة giggulf.ae لم تعد لازمة للطباعة أصلاً؛ باقي عمل GIG الديني لم يتغيّر · الجرعة الدائرية المعيارية محلياً والخدمة للمرة الأولى في الـgit (`Extension/forms/`) |
| 2026-11-06 | **Liva — تحسينات سرعة آمنة فقط (بلا مساس بمنطق البوابة):** (1) `stages[].ts_ms` — حقل قياس جديد بدقة ms في سجل الستاجات؛ `ts` الأصلي بلا مساس (الامتداد يقرأ step/status/detail فقط) · (2) كتابة الطبيب `delay=40 → 18ms` — أسرع من زمن استجابات autocomplete نفسها، وjQuery UI يتجاهل الردود القديمة عن التصميم · (3) **استُبعد عمداً دمج فحوص GSM** — توفير <0.2 ث مقابل خطر غير مبرر · (4) مثبت حياً بعد النشر وإعادة التشغيل: التدفق سليم كاملاً (زر Print → البوابة: already printed → Reprint → مطالبة `0845-2026-00034781` → PDF 222,812 بايت `%PDF-1.7`)، `doctor=EKLEEL ALI ADAM SAAD` · (5) ملاحظة قياس: الـjob أكمل في ~1.5 ث بفضل إعادة استخدام صفحة العضو (نفس الرقم + البيانات قائمة) — مسار البحث البارد يبقى +1–3 ث؛ الباقي (~2–4 ث) شبكة لبوابة ليفا نفسها ولا يمكن تجاوزه من المتصفح |
| 2026-11-06 | **Liva — إصلاح فصل المريض (الوثيقة كانت تُعاد لمريض آخر):** (1) **الجذر**: انتظار البحث كان `wait_for(ul.ui-autocomplete li || input.ui-autocomplete-input)` — يُرجع فوراً دائماً لأن jQuery UI يضيف الصنف ثابتاً على الصفحة ⇒ الوسيط يكمل اختيار الطبيب وdoAjax **قبل** رد `PreApproval/GetMemberInfo` ⇒ تُطبع مطالبة ببيانات من ما زالت معروضة (المريض السابق — النبضة تُعيد تحميل آخر رقم محفوظ) · (2) **الإصلاح**: النقر على Search يجري الآن داخل `expect_response("PreApproval/GetMemberInfo")` مع فحص `st.StatusCode == "S"` حرفياً (الرد الحقيقي للبحث)؛ أي كود آخر (مثلاً `N "Invalid Member!"` لعضو غير موجود) ⇒ توقف فوري برسالة `member_search_failed` — **لا طبيب، لا doAjax، لا طباعة** · (3) حماية الصفحة نصف الممسوحة: رقم محفوظ في الحقل مع حقول فارغة ⇒ بحث إجباري جديد · (4) حارس الهوية قبل الطباعة: `#txtMemberId` يجب أن يطابق المطلوب لحظتها وإلا `member_mismatch_before_print` · (5) جُرّبت السيناريو نفسه الواقع اليوم حياً: عضوان بنفس الطبيب — كل واحد يلتقط مطالبته (0845-2026-00034776 لـANAGHAM · ...477 لـSAID SUHAIL) وPDF مستقل لكل منهما · (6) member غير موجود ⇒ `status=N Invalid Member!` فشل صريح بلا أي أثر على البوابة · ملاحظة تقنية: `MemberCardNo` و`Memberobj` و`#txtCardNo` تُزامَن كلها داخل callback نجاح البحث نفسه — لذا الفحص على الرد (لا على DOM) هو الضمان الوحيد الصحيح |
| 2026-11-06 | **الامتداد v0.11.0 — مرفقات للطباعة (من HIS + من واتساب) مع الطباعة المجمّعة:** (1) **«من HIS»**: تجلب مستندات المريض المحفوظة (صور الهوية/إثبات التأمين) عبر `FileUpload/GetUploadedFiles` ثم `GetUploadedFilesByID` باستخدام `loggedInfo` + `AuthorizationToken` من الجلسة الحية في صفحة المشفى — قراءة فقط، لا يُحفظ أي شيء في سجلات المشفى · (2) **«من واتساب»**: آخر صورة من أحدث 5 محادثات واتساب ويب عبر `WAWebCollections.Chat` مع `downloadMedia({})` وبديل `blob:` من الـDOM · (3) **الطباعة المجمّعة**: المستند (GIG/فيبول/OQIC/MedNet/NextCare/Liva) + المرفقات المختارة في نافذة طباعة واحدة — كل جزء في صفحة A4 منفصلة (`insurer:printComposite`)؛ بدون مرفقات يُستخدم المسار المباشر `printHtml`/`printPdf` كما كان — الطباعة الأصلية بلا أي تغيير في الصياغة · (4) مثبت حياً: جلب مستندين من HIS للمريض 5101446 (26KB لكل منهما) + جلب صورة واتساب JPEG كاملة عبر `blob:` |
| 2026-11-07 | **الامتداد v0.14.0 — إصلاحات ملاحظات v0.13 (واتساب/مستندات HIS/الطباعة المجمّعة/تعبئة حقل الحفظ/تكبير المعاينة) + `POST /api/insurer/pdf-to-png`:** (1) **واتساب: صورة واحدة أعلى جودة بلا تحديد تلقائي** — `wa-images.js` تُعيد كتابتها لتجميع صور الرسالة الواحدة (مفتاح React `msgKey` ← `data-id` ← المصدر) والاحتفاظ بالأعلى دقة (أكبر `naturalWidth×naturalHeight`، وعند التساوي أكبر `dataUrl`)؛ قراءة أحدث الرسائل فقط (≤مصدرين لكل مجموعة) — مثبت حيّاً عبر CDP: 3 رسائل ⇒ 3 صور بلا تكرار (كان 5 مع تكرار) · أُزيل كل `selectedIds.add(...)` التلقائي (HIS وواتساب) — الموظف يختار ما يُطبع · (2) **مستندات HIS تتضمن PDF وتتحمّل الأنواع الشاذة** — فلتر `fetchHisDocuments` كان يسقط `application/pdf` والملفات ذات `FileType` فارغ؛ الآن يقبل صور+PDF ويستنتج الـMIME من الامتداد عند الحاجة + إعادة محاولة واحدة (1.2 ث) عند فشل عابر — مثبت حيّاً: المريض 5150231 (ملفه `HAMZA.PDF` فقط) ⇒ `printable:1` · (3) **الطباعة المجمّعة تُظهر كل الصفحات فعلياً** — سبب الجذر المثبت: `<embed>` PDF داخل HTML يُخرج **الصفحة الأولى فقط** في كروم (اختبار CDP: PDF 3 صفحات ⇒ مخرجات صفحة واحدة + شريط أداة) + إطار مخفي 2px وتأخير إزالة ثابت كانا يُفسدان المعاينة؛ الإصلاح: **تحويل PDF→PNG على الخادم** قبل بناء المستند (`pdfPartsToImages` → `/api/insurer/pdf-to-png`، Ghostscript، ≤24 صفحة) في مسارات الوسيط/الـpopup/وكيل NextCare · دوال حقن مشتركة `MCR_PrintComposite/Html/DataUrl` بإطار A4 مخفي (794×1123px خارج الشاشة)، انتظار الصور ≤4 ث، تنظيف عند `afterprint` فقط (أمان 300 ث)، واعتبار غياب `beforeprint` فشلاً مع احتياط داخل تبويب HIS (بلا نوافذ جديدة) · سجلات تشخيص صريحة (`log`) لأن `report()` لا يكتب السجل · (4) **تعبئة حقل المستندات في HIS عند اختيار صورة واتساب** — `his:fillFile` ⇒ `MCR_FillHisFile`: يضع الملف في `input[type=file]#file` (DataTransfer) ويستدعي معالج Angular مباشرة عبر `__ngContext__` (يفضّل المسار بلا تحجيم `onFileChangedwithoutresize` لضمان اكتمال `Patimage/filePreview` — مسار `ng2ImgMax` كان يتعطل) مع إرسال `change/input` احتياطاً — مثبت حيّاً: `onFileChangedwithoutresize` ⇒ `filePreview`/`Patimage`/`_patimgservice` ممتلئة وظهر `.snapshot` (3 صور في البطاقة) · التعبئة عند **تحديد** مرفق واتساب فقط (لا تلقائي) · (5) **تكبير/تصغير المعاينة بعجلة الماوس** — `#preview` قابلة للتمرير + مقياس 0.4–5 على الصورة/العنصر الداخلي (`wheel`، `passive:false`) + عنصر `#previewPdf` للمعاينة النصية لملفات PDF · (6) **الخادم (app.py، نُشر + أُعيد تشغيل `maxcall-dashboard`)**: مسار جديد `POST /api/insurer/pdf-to-png` (LAN فقط، `gs -sDEVICE=png16m -r72..300` افتراضي 200، تحقق `%PDF`، حد 60MB/24 صفحة) — مثبت حيّاً: `ok:true pages:1` · (7) **نشر**: `build-update.py --deploy` ⇒ `/ext/version.json` = **0.14.0** (كلا المضيفين) · `node --check` لكل الملفات المعدّلة + فحص اتساق IDs (32/32) + `py_compile` · **ملاحظة**: تغييرات `content script` (his-reader) تتطلب إعادة تحميل صفحة HIS بعد تحديث الإضافة |
| 2026-11-06 | **الامتداد v0.13.0 — واجهة إنجليزية مبسّطة + إغلاق تلقائي بعد الطباعة + إصلاح جذري للواتساب + الطباعة المجمّعة فعلياً:** (1) **واجهة إنجليزية فقط** — الـpopup أُعيد بناؤه `lang="en" dir="ltr"` بلا أي نص عربي وبلا قائمة خطوات (حُذفت `STEP_LABEL`/`addStep` كلياً): أزرار التذييل `Rc` (تسجيل التشخيص) و`Log`، زر المرفقات `Wa`، ورسائل حالة مختصرة واحدة فقط؛ شريط التقدم يظهر فقط في بنر صفحة المشفى (الخلفية تُغذّيه عبر `ui:progress`) · (2) **النافذة تُغلق بعد الطباعة** — `dispatchAndClose()` يرسل الأمر ثم `setTimeout(window.close, 250)` لمسارات `printHtml`/`printPdf`/`printComposite`، ومسار الوسيط (`insurer:run` ok) يُغلق فوراً — الموظف يرى التقدم في بنر المشفى لا في نافذة معلّقة · (3) **قراءة التأمين فورية** — `loadPatient` تعرض بطاقة الصفحة مباشرة من `buildCardFromPage` بلا انتظار `his:patient` (كان يعلّق على 401 `unauthorized` لفراغ بيانات الدخول) ثم إثراء صامت من السيرفر يصل لاحقاً بلا إعادة رسم إن لم يكن هناك فرق (حارس `state.excelTouched`) · (4) **مستندات HIS تُجلب تلقائياً** — زر «من HIS» حُذف؛ `autoLoadHisDocs()` تعمل مع كل قراءة مريض (مرة واحدة لكل `patId` عبر `hisDocsPatId`) وتحدد المرفقات تلقائياً · (5) **معاينة كبيرة** — النقر على أي صورة مصغّرة يفتح `#preview` بحجم كامل (الشبكة `#preview` LTR) · (6) **إصلاح جذري للواتساب**: السببان مثبتان حيّاً عبر CDP — أ) `executeScript` يعمل افتراضياً في العالم المعزول حيث `window.require` غير موجود ⇒ `no_result`؛ الآن `world:"MAIN"` + `.catch` داخلي + سباق مؤقّت 20 ث من الخدمة · ب) `downloadMedia({})` ينتهي `RESOLVED` لكن `mediaBlob`/`renderableUrl` يبقىان null في إصدار واتساب الحالي وحلقات `setTimeout` تتجمد في التبويب الخلفي ⇒ أُعيدت `wa-images.js` بالكامل **DOM-first** (صور المحادثة المفتوحة بـ`src` جاهز `data:`/`blob:` من الأحدث للأقدم، تفادي تكرار عبر React props `msgKey`، بلا أي مؤقّت) — مثبت حيّاً: 5 صور JPEG data URL مع اسم المحادثة · (7) **الطباعة المجمّعة تعمل فعلاً**: الجذر أن مسار الوسيط (`runBrokerInsurer`) لم يكن يخزّن المرفقات أبداً (`jobs` تُستخدم لتدفق النوافذ فقط) ⇒ `finishBrokerPrint` لا يجد شيئاً؛ الآن `brokerAttachments` Map تُضبط وتُمسح حول كل مهمة وسيط وتُقرأ في `finishBrokerPrint` — المستند PDF في الصفحة الأولى ثم الصور بعده في **دفعة واحدة** (`page-break-after` بين الأجزاء) · (8) **مسارات الطباعة المحلية تُغذّي البنر** (`report(step:"print")` في `printPdf`/`printHtml`/`printComposite`) · (9) **صفحة الإعدادات مبسّطة**: حقول user+pass لكل تأمين وزر `Save` فقط (Saving…/Saved/No changes/Error) بلا أي شرح — نفس API `GET/POST /api/insurer/creds` · (10) `node --check` لكل الملفات + فحص IDs بين `popup.html` و`popup.js` (لا ناقص) · `/ext/version.json` = 0.13.0 |
| 2026-11-06 | **الامتداد v0.12.0 — إصلاح ظهور المرفقات + واجهة عربية أرتب + حفظ الإعدادات على السيرفر:** (1) **إصلاح عكس منطق بطاقة المرفقات** — `renderAttachments` كانت تخفي البطاقة حين القائمة فارغة وتُستدعى فقط عند وجود تأمين ⇒ أزرار «من HIS/من واتساب» تظهر فقط **بدون** تأمين (العكس المقصود)؛ الآن البطاقة ظاهرة دائماً مع التأمين المدعوم بحالة فارغة واضحة وزر «مسح الكل» · (2) **الـpopup أعيد هيكلته عربياً** (RTL، ترتيب: المريض ← التأمين ← الحقول ← المرفقات ← زر الطباعة، كل الرسائل/الخطوات بالعربية) — نفس IDs والوظائف بلا أي تغيير منطقي · (3) **زر «من HIS» يعمل مع أي مريض محفوظ**: `patId` من الرابط (`PatientRegistration/<id>`) أو من الحقل المستقر `#txtPat_ID` (اكتشاف حي: القيمة ظاهرة فيه حتى عند فتح زيارة برقم آخر) · (4) **صفحة الإعدادات أُعيد بناءها**: كلمات مرور التأمين + اسم المستخدم فقط (nextcare/mednet/liva) — الحفظ **يُرسل إلى السيرفر** لا محلياً؛ كلمة مرور فارغة = تبقى الحالية على السيرفر · (5) **حفظ بيانات الدخول من السيرفر**: مسارات `GET/POST /creds` في `insurer_broker.py` — الكتابة عينية (`tmp` + `os.replace` + chmod 600) على `/etc/maxcall-insurers.json` **بلا restart** (كاش mtime)؛ GET يرد مقنّعاً (user + has_pass بلا كلمة) · `app.py` يوسّط عبر `GET/POST /api/insurer/creds` (LAN فقط، مثل بقية `/api/insurer/*`) · مثبت حياً: POST بنفس القيم ⇒ محتوى الملف متطابق حرفياً والمفاتيح والصلاحيات 600 محفوظة · تغيير كلمة Liva من الصفحة يفتح القفل تلقائياً (نفس آلية creds-stamp) · `/ext/version.json` = 0.12.0 |
| 2026-11-06 | **استدعاء الأطباء — تصحيح "DR. SAYED MUSTHAFA KHALIL":** الربط كان خاطئاً ("Elsayed Mustafa Khalil Elsharkawy") — الصحيح في ليفا **Elsayed Mustafa**؛ أُضيفت مفاتيح `SAYED MUSTAFFA KHALIL` و`SAYED MUSTHAFA KHALIL` و`SAYED UROLOGIST` له (نفس الطبيب الوحيد المسجل باسم SAYED) · التحقق عبر `_match_doctor` نفسه بقائمة ليفا الفعلية — كل الإملاءات تُطابق الآن · ذاكرة `doctor_aliases.json` + عملها التلقائي مؤكد (EKLEEL ALI ADAM SAAD حُفظت بنفسها بعد اختيار ناجح) |
| 2026-11-05 | **الجاهزية الدائمة (liveness-first) — استعادة تلقائية كاملة + كابح Liva فقط:** (1) **Liva**: النبضة تعيد بناء الاستيقاظ كاملة بذاتها (VPN → كابتشا تلقائية H6 بتحقق `/Dashboard/` → صفحة العضوية → تحقق eligibility) بدل "defer إلى مهمة حقيقية" — ثبت حياً بعد موت البوابة 18:19 (استعادة 14.1 ث بلا موظف) · حدود أمان: 10 استعادة بوابة/ساعة، فاصل 4 دقائق، 4 بناء بارد/ساعة · (2) **MedNet**: النبضة على أي حالة — goto → re-login (كلمة المرور فقط) → ملاحة للشاشة (JS_NAV_LINK ← موسّع لبنى JSF المختلفة ← **بناء بارد كامل**) → تحقق eligibility — كان يفشل منذ بدايات الليلة `member_screen_not_found` (~189 نبضاً) الآن يستعيد نفسه · (3) **NextCare**: تبقى على إعادة تحميل صفحة الاستعلام (كانت تنجو دائماً) — أُضيف تتبع الجلسة لها كذلك · (4) **كابح Liva فقط**: رفض FortiGate صريح (`err=...permission_denied` أو "Too many bad login attempts") ⇒ توقف تام لـLiva (بلا نبضات، بلا مهام، بلا كابتشا) حتى تتغير `/etc/maxcall-insurers.json` ⇒ القفل ينفتح تلقائياً وتعود الاستعادة — **MedNet/NextCare لا علاقة لهما بالقفل إطلاقاً؛ عند قفل Liva يقفل ليفا وحده ويكمل الآخرون** · (5) **`session-events.jsonl`** — كل موت/استعادة/قفل بسطر JSON (timestamps, dead_for_s, URL) + `/opt/maxcall-broker/tools/broker_session_stats.py` + حقول جديدة في `/health` (`locked`, `lockReason`, `lastOk`, `lastDead`, `lastRestore`, `restoresTotal`, `sessionAgeS`, `lastEvent`) · (6) **مدقق الجذر**: بوابة Liva تنتهي مطلقاً بحوالي 20-30 دقيقة حتى مع النشاط (أثبتت السجلات: كل النبضات منذ 00:00 كانت تسجل "portal re-auth deferred" — البوابة كانت ميتة اليوم كله ولم يستعدها أحد) — الاستعادة التلقائية تَعِد بالجاهزية رغم ذلك · (7) أثناء النشر (تحريف خطأ نسخ `brokers/` إلى جذر العملية) حدث Crash-loop — أُعيدت النسخة الصحيحة للمسار `/opt/maxcall-broker/brokers/` وتعافى الوسيط خلال دقائق |
| 2026-11-05 | **Liva — الطباعة تلقائية بالكامل (لا كابتشا موظف):** (1) **ddddocr** — مكتبة OCR أوفلاين مُثبتة في `/opt/maxcall-broker/venv` تقرأ كابتشا البوابة تلقائياً (~90-98% ثقة؛ الموقع يُصدر كابتشا جديدة عند كل رفض ⇒ حتى 6 محاولات)، والتحقق من النجاح = وصول إلى `/Dashboard/` (ليس اختفاء `#CaptchaInputText` — يختفي أثناء إعادة البناء بعد الرفض!)؛ عند فشل 6 محاولات ⇒ يطلب من الموظف كالمعتاد؛ (2) **نقرة JS مباشرة لزر Reprint** — `trusted_click` (CDP بالإحداثيات) كانت تفشل لأنه `type="submit"` وأحياناً مغطّى بصيرورة `.Loading`، والمعالج delegated عبر jQuery على document؛ (3) **التقاط PDF كامل:** Reprint ⇒ مودال `#MemberPop` ⇒ جدول `#PreAprovals` (claim لكل طبيب+يوم) ⇒ اختيار الأحدث (SELVAGANESH 04-Nov-2026) ⇒ `Reports/FormDetail` ⇒ `window.open(FilePath)` ⇒ PDF 221KB حقيقي بـ 3 ثوانٍ — **بلا إنشاء أي مستند جديد**؛ (4) **تجميد تجديد الكابتشا** (`ctx.route` مُجهض `DefaultCaptcha/Refresh`) — صورة ثابتة وصالحة طوال المهلة (العيب الأصلي: الصورة تدور كل 60 ث فتبطل إجابة المتأخر)؛ (5) **سباق Angular مُصحح** — صفحة الدخول تحتاج ~2.5 ث ليربط معالج النموذج؛ الإرسال المبكر كان يفقد النقرة ويُرفض الكابتشا بلا سبب؛ (6) التحقق من النجاح أُضيف في `resume_after_captcha` — نفس منطق URL `/Dashboard/` بدل `#CaptchaInputText`؛ (7) مهلة الكابتشا الكلية 300→600 ث. **الدورة كاملة مستقلة ~25 ث:** VPN → كابتشا (auto) → member → doctor → GSM → doAjax (F) → Reprint → modal → claim → FormDetail → window.open → PDF ✅ مُعتمدة حياً مرتين |
| 2026-11-04 | **Liva Phase 2.2 — fallback ذكي:** (1) اختيار الطبيب: ذاكرة مطابقات `/var/lib/maxcall-broker/doctor_aliases.json` تُملأ تلقائياً + fuzzy (difflib، وزن بالطول) — SELVAGANESAN↔SELVAGANESH تطابق تلقائياً؛ (2) عند رفض البوابة مكرراً ("Claim Form already printed") يُغلق التنبيه ويُفتح "Reprint Online Claim Form" تلقائياً، ويُختار صف الطبيب الأحدث ويُفتح PDF الـClaim — **بلا إنشاء أي مستند جديد**؛ (3) شاشة الكابتشا بمهلة 90 ث (كانت 15). |
| 2026-11-04 | **نبضة Liva مُثبتة حياً:** تلقائياً 15:17:04 (بعد نبضات MedNet/NextCare 15:15) — eligibility re-check على MemberDetails خلال 3 ثوانٍ بلا أي تنقّل وبلا re-login؛ الجلسة تُصان تلقائياً كل 4 دقائق |
| 2026-11-04 | **نبضة Liva (Phase 2.1):** keepalive = تحقق eligibility برقم التأمين على صفحة MemberDetails نفسها (نفس فكرة MedNet — لا طباعة، لا إيصال، لا مغادرة للصفحة، لا إعادة تحميل)؛ VPN يُجدد تلقائياً (حد أقصى دخول واحد في الساعة)؛ إن كانت جلسة بوابة المزوّد منتهية → تذوب النبضة بأمان وتنتظر كابتشا المهمة الحقيقية (مطلوبة بعد أي خمول طويل — الخادم ينهي جلسات البوابة)؛ `heartbeat.json.liva` تُملأ تلقائياً بعد أول Search ناجح (زُرعت مؤقتاً برقم آخر بحث اليوم حتى تعمل النبضة قبل أول عملية حقيقية) |
| 2026-11-04 | **Liva عبر الوسيط (Phase 2) — 0.9.21:** `server/brokers/liva.py`: VPN FortiGate (`/remote/login`) بجلسة دائمة → بطاقة Provider (تُفتح window.open، تُلتقط وتُوجَّه داخل نفس التبويب) → بوابة المزوّد Angular → **كابتشا تُلتقط بعنصر `#CaptchaImage`** وتُعرض على شاشة الموظف عبر نفس قناة `state=captcha` (PayLoad type=captcha — 90 ث lease دوّار بين الأجهزة؛ انتهاء العداد = بلا إجابة تُرسل بدل إجهاض المهمة للجميع) → صفحة العضوية (`#txtMemberId` + `#btnSearch`) → الطبيب (jQuery UI — مطابقة HIS بلا حساسية لحالة الأحرف؛ فشل ⇒ **قائمة اختيار 75 ث** في HIS — `NeedsChoice` → نفس قناة الإجابة type=choice) → الهاتف `968-` (قناع mask + `setSelectionRange` + إدخال رقم-رقم) → انتظار امتلاء `#txtSpeciality` → طباعة `button.Printmember` → PDF يُلتقط بمعترض window.open (`\.pdf|pdfdisplayservlet|ReportsViewer`). النبضة لـ Liva = تجديد VPN فقط (لا تلمس كابتشا البوابة). تم نشر الوسيط واختباره حياً حتى نقطة الكابتشا (~8 ث من الطلب حتى ظهورها)؛ إتمام الطباعة يتطلب موظفاً يكتب الرمز |
| 2026-11-04 | نشر 0.9.20/0.9.19 (MedNet+NextCare): استرجاع إعادة تحميل صفحة الاستعلام fresh لكل طلب + نشر PDF فقط بعد مطابقة عارض حقيقي + 4 جولات متتالية ناجحة لـ NextCare حياً + تنظيف مرتقيات التقاط التقرير (`.ashx` العامة مرفوضة) |
| 2026-11-04 | الإضافة تلتقط طلبات التأمين للوسيط وتعرض تقدماً على صفحة HIS وتطبع PDF داخل الصفحة بلا نوافذ؛ الإزالة التامة لأي fallback محلي؛ `/api/insurer/*` محصور على LAN (`ipaddress.is_private`) |
| 2026-10-16 | توثيق مسار مرآة التحديث `GET /updates/<name>` (عام بلا مصادقة، ٦ أسماء مسموحة من `/opt/maxcall-updates`) في جدول هذا القسم + إنشاء `UPDATE_REFERENCE.md` كمرجع كامل لميزة التحديث الذاتي |
