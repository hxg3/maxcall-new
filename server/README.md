# server/ — مصدر السيرفر المعتمد (نفس نمط Dashboard/)

هذه الملفات هي النسخ المعتمدة لما يعمل على السيرفر `192.168.1.165`.
أي تعديل هنا يُنشر يدوياً ثم يُختبر قبل الانتقال لغيره.

## النشر

```bash
# 1) تطبيق الـ API (يتطلب restart)
scp server/app.py root@192.168.1.165:/tmp/ && ssh root@192.168.1.165 \
  "cp /tmp/app.py /opt/maxcall-dashboard/app.py && systemctl restart maxcall-dashboard"

# 2) الداشبورد (قالب Jinja مخزّن في ذاكرة gunicorn — restart إلزامي)
scp Dashboard/dashboard.html root@192.168.1.165:/tmp/ && ssh root@192.168.1.165 \
  "cp /tmp/dashboard.html /opt/maxcall-dashboard/templates/dashboard.html && systemctl restart maxcall-dashboard"

# 3) خطة الاتصال والطابور (reload فقط)
scp server/extensions__99-maxcall.conf server/queues__99-maxcall.conf root@192.168.1.165:/tmp/ && ssh root@192.168.1.165 \
  "cp /tmp/extensions__99-maxcall.conf /tmp/queues__99-maxcall.conf /etc/asterisk/vitalpbx/ && asterisk -rx 'dialplan reload' && asterisk -rx 'queue reload all'"
```

## الملفات

| الملف | المنشور إلى |
|---|---|
| `app.py` | `/opt/maxcall-dashboard/app.py` (خدمة `maxcall-dashboard`) |
| `presence_cron.py` | `/opt/maxcall-dashboard/presence_cron.py` |
| `extensions__99-maxcall.conf` | `/etc/asterisk/vitalpbx/` |
| `queues__99-maxcall.conf` | `/etc/asterisk/vitalpbx/` (الأعضاء تُدار تلقائياً عبر `_queue_sync` — لا تعديل يدوي) |
| `maxcall-asterisk.sh` | `/usr/local/bin/maxcall-asterisk` (755 + sudoers: `maxcall ALL=(root) NOPASSWD: ...`) |
| `maxcall-backup.sh` | `/usr/local/bin/maxcall-backup` (كرون يومي 03:30 → `/var/backups/maxcall`) |
| `maxcall-gitbackup.sh` | `/usr/local/bin/maxcall-gitbackup` (كرون 03:45 → ريبو `hxg3/maxcall-backup`: قواعد+إعدادات+تطبيق، مشفر age، 30 يوماً) |
| `maxcall-gitrec.sh` | `/usr/local/bin/maxcall-gitrec` (كرون 04:15 → ريبو `hxg3/maxcall-recordings`: تسجيلات، نافذة 30 يوماً + حارس 4GB + squash شهري) |

التفاصيل الكاملة في `SERVER_REFERENCE.md`.
