import { getSettings, saveSettings } from "../lib/settings.js";
import { hisPatient } from "../lib/server.js";

const $ = (id) => document.getElementById(id);

function fill(s) {
  $("serverUrl").value = s.serverUrl;
  $("serverUser").value = s.serverUser;
  $("serverPass").value = s.serverPass;
  $("nextcareUser").value = s.insurers.nextcare.user;
  $("nextcarePass").value = s.insurers.nextcare.pass;
  $("mednetUser").value = s.insurers.mednet.user;
  $("mednetPass").value = s.insurers.mednet.pass;
  $("livaUser").value = s.insurers.liva.user;
  $("livaPass").value = s.insurers.liva.pass;
  $("autoPrint").checked = s.autoPrint;
}

function collect() {
  return {
    serverUrl: $("serverUrl").value.trim(),
    serverUser: $("serverUser").value.trim(),
    serverPass: $("serverPass").value,
    autoPrint: $("autoPrint").checked,
    insurers: {
      nextcare: {
        enabled: true,
        user: $("nextcareUser").value.trim(),
        pass: $("nextcarePass").value,
      },
      mednet: {
        enabled: true,
        user: $("mednetUser").value.trim(),
        pass: $("mednetPass").value,
      },
      liva: {
        enabled: true,
        user: $("livaUser").value.trim(),
        pass: $("livaPass").value,
      },
    },
  };
}

$("save").addEventListener("click", async () => {
  await saveSettings(collect());
  const out = $("testOut");
  out.textContent = "تم الحفظ ✓";
  out.className = "ok";
  setTimeout(() => { out.textContent = ""; out.className = ""; }, 2500);
});

$("test").addEventListener("click", async () => {
  await saveSettings(collect());
  const out = $("testOut");
  out.textContent = "جاري الاختبار…";
  out.className = "";
  // رقم 1 غير موجود غالباً — الهدف التحقق من الوصول والمصادقة لا من المريض
  const res = await hisPatient("1");
  if (res.ok || res.reason === "not_found") {
    out.textContent = "الاتصال سليم ✓";
    out.className = "ok";
  } else {
    const map = {
      unauthorized: "بيانات السيرفر غير صحيحة ✕",
      server_unreachable: "السيرفر غير متاح ✕",
      server_not_configured: "العنوان فارغ ✕",
      timeout: "مهلة انتهت — تحقق من العنوان ✕",
      rate_limited: "محدودية الطلبات — أعد بعد لحظات ✕",
    };
    out.textContent = map[res.reason] || `فشل: ${res.reason}`;
    out.className = "err";
  }
});

getSettings().then(fill);
