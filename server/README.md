# server/ — مصدر السيرفر المعتمد (نفس نمط Dashboard/)

هذه الملفات هي النسخ المعتمدة لما يعمل على السيرفر `maxcall.local`.
أي تعديل هنا يُنشر يدوياً ثم يُختبر قبل الانتقال لغيره.

## النشر

```bash
# 1) تطبيق الـ API (يتطلب restart)
scp server/app.py root@maxcall.local:/tmp/ && ssh root@maxcall.local \
  "cp /tmp/app.py /opt/maxcall-dashboard/app.py && systemctl restart maxcall-dashboard"

# 2) الداشبورد (قالب Jinja مخزّن في ذاكرة gunicorn — restart إلزامي)
scp Dashboard/dashboard.html root@maxcall.local:/tmp/ && ssh root@maxcall.local \
  "cp /tmp/dashboard.html /opt/maxcall-dashboard/templates/dashboard.html && systemctl restart maxcall-dashboard"

# 3) خطة الاتصال والطابور (reload فقط)
scp server/extensions__99-maxcall.conf server/queues__99-maxcall.conf root@maxcall.local:/tmp/ && ssh root@maxcall.local \
  "cp /tmp/extensions__99-maxcall.conf /tmp/queues__99-maxcall.conf /etc/asterisk/vitalpbx/ && asterisk -rx 'dialplan reload' && asterisk -rx 'queue reload all'"
```

## الملفات

| الملف | المنشور إلى |
|---|---|
| `app.py` | `/opt/maxcall-dashboard/app.py` (خدمة `maxcall-dashboard`) |
| `his_broker.py` | `/opt/maxcall-dashboard/his_broker.py` (وساطة Solver WebHIS — قراءة فقط) |
| `presence_cron.py` | `/opt/maxcall-dashboard/presence_cron.py` |
| `extensions__99-maxcall.conf` | `/etc/asterisk/vitalpbx/` |
| `queues__99-maxcall.conf` | `/etc/asterisk/vitalpbx/` (الأعضاء تُدار تلقائياً عبر `_queue_sync` — لا تعديل يدوي) |
| `maxcall-asterisk.sh` | `/usr/local/bin/maxcall-asterisk` (755 + sudoers: `maxcall ALL=(root) NOPASSWD: ...`) |
| `maxcall-backup.sh` | `/usr/local/bin/maxcall-backup` (كرون يومي 03:30 → `/var/backups/maxcall`) |
| `maxcall-gitbackup.sh` | `/usr/local/bin/maxcall-gitbackup` (كرون 03:45 → ريبو `hxg3/maxcall-backup`: قواعد+إعدادات+تطبيق، مشفر age، 30 يوماً) |
| `maxcall-gitrec.sh` | `/usr/local/bin/maxcall-gitrec` (كرون 04:15 → ريبو `hxg3/maxcall-recordings`: تسجيلات، نافذة 30 يوماً + حارس 4GB + squash شهري) |

التفاصيل الكاملة في `SERVER_REFERENCE.md`.

## متغيرات وحدة WebHIS (his_broker.py)

توضع في `/etc/maxcall.env` (نفس آلية الحقن الحالية عبر systemd) ثم `systemctl restart maxcall-dashboard`:

```bash
HIS_USERNAME=abdullah      # حساب قراءة في WebHIS — يفضَّل حساب read-only مخصص
HIS_PASSWORD=...           # يبقى على السيرفر فقط ولا يصل للعميل إطلاقاً
HIS_HOST=192.168.1.77      # IP سيرفر WebHIS
HIS_PORT=81
```

* غيابهما (أو كلمة مرور خاطئة) → `GET /api/his/caller/<phone>` يعيد `{"ok":false}`
  فيُخفي قسم Patient File بهدوء، وتعمل بقية النافذة المنبثقة كالمعتاد.
* الوسيط يجدِّد التوكن تلقائياً (وفق تاريخ الانقضاء `exp` مع هامش 65 يوماً)
  ويعيد الدخول مرة واحدة عند 401/403، ويخزن النتائج 10 دقائق (LRU حديدها 500).
* عمليات WebHIS الأربع المسموح بها ثابتة ترميزياً — لا يوجد بروكسي عام إطلاقاً.
* التوثيق على مسار HIS: جلسة الداشبورد أو Basic auth ببيانات الموظف نفسها
  التي تحققها `/api/agent/login` — لا آلية دخول موازية.
* اختبارات القبول الكاملة (قبالة WebHIS الحقيقية):
  `cd server && HIS_USERNAME=... HIS_PASSWORD=... python3 test_his_broker.py`

## تحصينات موصى بها (لم تُنفذ — للـ operator)

* جدار ناري يحصر المنفذ 3001 على شبكة MaxCall + TLS أمامه (nginx) — بيانات طبية تنتقل عبره.
* حساب WebHIS مخصص read-only للاستخدام الآلي بدل حساب شخصي.
