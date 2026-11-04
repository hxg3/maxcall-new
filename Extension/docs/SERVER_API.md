# واجهة السيرفر — `/api/his/patient/<pat_id>`

مسار قراءة فقط أُضيف لخدمة الإضافة، بنفس نمط وأمان `/api/his/caller` تماماً.

## المواصفة

```
GET http://maxcall.local:3001/api/his/patient/5080625
Authorization: Basic <extension>:<secret>     # نفس بيانات جهاز MaxCall
```

- **المصادقة:** جلسة داشبورد أو Basic auth ببيانات موظف/جهاز (ombu_extensions + ombu_devices) —
  نفس `_require_his_api` — بلا `WWW-Authenticate` حتى لا تظهر نوافذ كلمة مرور.
- **Rate-limit:** 30 طلب/60 ثانية لكل IP (نفس حارس `/api/his/caller`).
- **الاستجابة:** allow-list محدد في `his_broker.insurance_card()` — لا شيء آخر يخرج من الوسيط.

### الاستجابة الناجحة

```json
{
  "ok": true,
  "patient": {
    "pat_id": "5080625", "name": "PREM CHAND . .", "sex": "M", "age": "42Y",
    "dob": "1984-04-15", "phone": "98919630", "country_code": "+968", "nationality": "INDIA"
  },
  "insurance": {
    "payer_code": "C0322", "payer_name": "NEXTCARE - DHOFAR INSURANCE",
    "company_code": "C0322", "card_no": "0EEDF9B48F2C63E3",
    "policy_no": "41/713/2026/2", "card_expiry": "2027-12-30",
    "deductible": 0.0, "co_ins_percent": 0.0
  },
  "visit": {
    "consultant": "DR. K. SELVAGANESAN", "consultant_id": "D103", "speciality": "ORT",
    "appointment_date": null, "appointment_consultant": null
  },
  "source_url": "http://solverserver:81/#/home/hmis/op/PatientSearch"
}
```

### الأخطاء

| الحالة | الجسم |
|---|---|
| 401 | `{"ok":false,"reason":"unauthorized"}` |
| 429 | `{"ok":false,"reason":"rate_limited"}` |
| 400 (رقم غير صالح) | `{"ok":false,"reason":"bad_pat_id"}` |
| 200 (مريض غير موجود) | `{"ok":false,"reason":"not_found"}` |
| 200 (WebHIS غير متاح) | `{"ok":false,"reason":"his_unreachable"}` إلخ |

## المصادر في WebHIS (قراءة فقط — استدعاءان ثابتان)

1. `patient/GetPatientByID` بـ`{"Pat_ID": "<رقم كنص>"}` — كل حقول المريض والبطاقة
   (`Pat_Certificate_No` = رقم بطاقة التأمين، `Pat_Policy_No` = رقم الوثيقة).
2. `PatientSearch/GetSearchResult` بفلتر `Pat_ID` — اسم الدافع (`CompanyName`).

ملاحظات موثقة في `his-integration/HIS_DATA_MAP.md`:
endpoints مثل `Appointment/GetWebInsuranceDetails` **غير موجودة** فعلياً على السيرفر رغم ورودها في JS الواجهي.
يُحكم دائماً من `Payload` لا من حقل `Message` (غرابة WebHIS).

## النشر على السيرفر (maxcall.local)

```bash
# 1) نسخ احتياطي على السيرفر
ssh root@maxcall.local 'cp /opt/maxcall-dashboard/app.py /opt/maxcall-dashboard/app.py.bak-<tag> && \
  cp /opt/maxcall-dashboard/his_broker.py /opt/maxcall-dashboard/his_broker.py.bak-<tag>'

# 2) النسخ
scp server/app.py server/his_broker.py root@maxcall.local:/opt/maxcall-dashboard/

# 3) إعادة التشغيل والتحقق
ssh root@maxcall.local 'systemctl restart maxcall-dashboard && systemctl is-active maxcall-dashboard'
```

ملاحظات:
- الخدمة تستمع على المنفذ **3001**.
- بيانات HIS (`HIS_USERNAME/HIS_PASSWORD`) في `/etc/maxcall.env` — تبقى على السيرفر فقط.
- قبل أي نشر: `python3 -m py_compile server/app.py server/his_broker.py` محلياً،
  و`diff` ضد نسخة السيرفر للتأكد أن الفرق = تغييراتك فقط.


## `/api/insurer/*` — وسيط المستندات على السيرفر (LAN فقط)

الإضافة تقرأ المتغيرات من HIS وتطلب وتستعرض PDF — لا تشغيل لأي موقع تأمين
على جهاز الموظف إطلاقاً. الوساطة على `maxcall.local:3001` وكل المواقع تشتغل
داخل خدمة `maxcall-broker` على `127.0.0.1:3011`.

**لا تسجيل دخول لكل موظف** — الوصول من شبكة MaxCall الداخلية فقط:

```python
import ipaddress, socket
def _lan_only():
    ip = ipaddress.ip_address(request.remote_addr or "")
    return ip.is_private
```

| المسار | النتيجة |
|---|---|
| `GET /api/insurer/health` | حالة العمال (alive/busy/queue/sessionWarm/captchaPending· للتأمين) — `mednet` · `nextcare` · `liva` |
| `POST /api/insurer/job` | body: `{insurer:"mednet"\|"nextcare"\|"liva", member:"919492"\|"B624...", card:{...}}` → `{ok, jobId, queuePos}` |
| `GET /api/insurer/job/<id>` | `{state, stages[], queuePos, result}` — `?pdf=1` يضيف `pdf_b64` عند `state=done`؛ عند `state=captcha` ينقل `captcha:{type,img_b64\|options,title?,hint?,lease,token}` |
| `POST /api/insurer/job/<id>/answer` | body `{token, text}` لإجابة التحقق البشري — كابتشا (عقد 15 ث) أو اختيار من قائمة (عقد 75 ث) — تنقّل تلقائي بين الأجهزة؛ أول إجابة تُقبل والبقية مرفوضة؛ إجابة فارغة = إلغاء المهمة |

**انشرت Liva عبر الوسيط (0.9.21):** بوابة FortiGate SSL-VPN على السيرفر (الجلسة الدافئة في النبضة — مركبة بدل تحميل من الصفر)، ثم بوابة المزوّد Angular (نفس بيانات الدخول + كابتشا تُعرض على شاشة الموظف — مطلوبة عند كل دخول للبوابة)، ثم صفحة العضوية: Search → اختيار الطبيب (مطابقة مع اسم HIS؛ إن تعذر تظهر قائمة اختيار للموظف بمهلة 75 ث) → الهاتف `968-` → امتلاء الاختصاص → طباعة PDF في HIS.
**النبضة (keepalive) لـ Liva:** تحقق eligibility برقم التأمين **داخل صفحة MemberDetails نفسها** كل 4 دقائق (لا طباعة، لا إيصال، لا مغادرة للصفحة ولا إعادة تحميل) — نفس آلية MedNet/NextCare. لا تقوم البوابة بالتحقق إن كانت جلسة بوابة المزوّد منتهية (تتطلب كابتشا — تُترك للمهمة الحقيقية)؛ VPN يُجدد تلقائياً بحد أقصى دخول واحد في الساعة.
- الكابتشا: `captcha.type="captcha"` — `img_b64` (JPEG) تُعرض في HIS مع عدّاد؛ عند الانقضاء تذوب تلقائياً وتنتقل الرخصة للجهاز التالي (لا إجابة فارغة تُرسل).
- الاختيار: `captcha.type="choice"` — `options[]` + `title` + `hint`؛ النقر على عنصر يُرسله كإجابة.

- التقدم: يصل كـ `stages[]` مع `ts` — الإضافة تنسخه لشريطها على صفحة المشفى.
- أخطاء خارج المنطقية (السيرفر مطفأ / موظف غريب): `503 {"ok":false,"error":"broker_unavailable"}` —
  الإضافة تبلغ: "تعذّر استخراج المستند — نفّذ العملية يدوياً كما تعودت".

## التراجع

```bash
ssh root@maxcall.local 'cp /opt/maxcall-dashboard/app.py.bak-<tag> /opt/maxcall-dashboard/app.py && \
  cp /opt/maxcall-dashboard/his_broker.py.bak-<tag> /opt/maxcall-dashboard/his_broker.py && \
  systemctl restart maxcall-dashboard'
```

## اختبار سريع

```bash
curl -u "<extension>:<secret>" http://maxcall.local:3001/api/his/patient/5080625
curl -o /dev/null -w "%{http_code}\n" http://maxcall.local:3001/api/his/patient/5080625   # 401 بدون auth
```
