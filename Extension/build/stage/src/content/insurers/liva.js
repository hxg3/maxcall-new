(() => {
  // Liva / NLI Gulf — عُمان.
  //
  // التدفق الكامل (كله داخل تبويب الموقع — لا نوافذ منبثقة):
  //  1) https://online.nlicgulf.com:12443/remote/login?lang=en
  //     بوابة FortiGate SSL-VPN: اسم مستخدم + كلمة مرور → دخول.
  //  2) لوحة البوابة: بطاقة "Provider" → تفتح تبويب البوابة الداخلية (proxy).
  //  3) بوابة المزوّد: نفس البيانات + كابتشا صورة (#CaptchaInputText).
  //     الكابتشا تُعرض في صفحة المشفى — يحلّها الموظف من هناك (لا موظف آلي).
  //  4) "Member Eligiblity & Online Claim Form" → صفحة MemberDetails.
  //  5) رقم العضوية (#txtMemberId + Enter) → اختيار الطبيب من قائمة jQuery UI
  //     → الهاتف (#txtGsmNo) → "Print Online Claim Form" → PDF يُطبع في صفحة الموظف.
  //
  // مبادئ التصميم (مثل بقية المحوّلات):
  //  • الحالة تُقرأ من الـDOM في كل خطوة (idempotent) — لا علم "تم النقر".
  //  • لا انتظار ثابت — كل خطوة تنتظر إشارة فعلية من الصفحة.
  //  • الطباعة ليست مسؤولية المحوّل: bridge.printReport يعترض الرابط في عالم
  //    الصفحة، والخلفية تجلبه وتطبعه داخل تبويب المشفى.

  const SEL = {
    member: "#txtMemberId",
    gsm: "#txtGsmNo",
    captcha: "#CaptchaInputText",
    captchaImg: "#CaptchaImage",
    printBtn: "button.Printmember",
  };

  // نمط روابط التقرير — للـwebRequest فقط. "generate" استُبعدت تعمداً:
  // هي نفسها في رابط صورة الكابتشا (DefaultCaptcha/Generate).
  // ملاحظة: مسارات api/Reports/ (PatientInfo, GetClaimFormList, FormDetail)
  // تعيد JSON فيه FilePath — ليست PDF أبداً، والتقاطها كان يبلع معالجة
  // رابط الملف الحقيقي؛ FilePath يصل عبر تنصت استجابة عالم الصفحة.
  const REPORT_RE_SRC = "\\.pdf|pdfdisplayservlet|ReportsViewer";

  // مفتاح تذكّر رقم العضوية الذي بُحث عنه (يبقى عبر إعادة تحميل الصفحة)
  const SEARCH_KEY = "mcrLivaSearchedMember";
  // عدّادات محاولات الدخول (تبقى عبر إعادة تحميل الصفحة — تمنع الحلقات)
  const VPN_KEY = "mcrLivaVpnAttempts";
  const PROVIDER_KEY = "mcrLivaProviderAttempts";
  // لحظة إرسال دخول المزوّد — بها نتجاهل صفحة الدخول الانتقالية بعد النجاح
  const SUBMIT_KEY = "mcrLivaProviderSubmitAt";
  // عنوان الصفحة عند تحميل السكربت — لكشف أي تنقل حصل بعد النقر
  const START_HREF = location.href;

  // ------------------------------------------------------------------
  // أدوات DOM عامة
  // ------------------------------------------------------------------
  function text(el) {
    if (!el) return "";
    const t = String(el.textContent || "").replace(/\s+/g, " ").trim();
    if (t) return t;
    return String(el.value || "").replace(/\s+/g, " ").trim();
  }

  function visible(el, bridge) {
    return !!el && bridge.isVisible(el);
  }

  function byText(selector, re, bridge) {
    return (
      [...document.querySelectorAll(selector)].find(
        (el) => visible(el, bridge) && re.test(text(el))
      ) || null
    );
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(fr.result);
      fr.onerror = reject;
      fr.readAsDataURL(blob);
    });
  }

  // عدّاد محاولات في sessionStorage — يبقى عبر إعادة تحميل الصفحة
  function bump(key) {
    const n = Number(sessionStorage.getItem(key) || 0) + 1;
    try { sessionStorage.setItem(key, String(n)); } catch (e) { /* ignore */ }
    return n;
  }

  // أحداث لوحة المفاتيح — jQuery UI autocomplete يستجيب لـkeyup، ونماذج
  // الموقع تستجيب لـkeypress (ValidateMemberNumber) — نطلق الثلاثة.
  function fireKey(el, key, code, keyCode) {
    for (const type of ["keydown", "keypress", "keyup"]) {
      const ev = new KeyboardEvent(type, {
        key,
        code,
        bubbles: true,
        cancelable: true,
      });
      try {
        Object.defineProperty(ev, "keyCode", { get: () => keyCode });
        Object.defineProperty(ev, "which", { get: () => keyCode });
      } catch (e) { /* ignore */ }
      el.dispatchEvent(ev);
    }
  }

  const pressEnter = (el) => fireKey(el, "Enter", "Enter", 13);
  const typeLetter = (el) => fireKey(el, "a", "KeyA", 65);

  function clickLikeUser(el) {
    const opts = { bubbles: true, cancelable: true, view: window, button: 0, detail: 1 };
    for (const type of ["mousedown", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, opts));
    }
  }

  // ------------------------------------------------------------------
  // كشف الصفحات
  // ------------------------------------------------------------------
  function isProviderLogin() {
    return !!document.querySelector(SEL.captcha);
  }

  function isMemberPage() {
    return !!document.querySelector(SEL.member);
  }

  function isVpnLogin(bridge) {
    if (/\/remote\/login/i.test(location.pathname + location.search)) return true;
    if (isProviderLogin() || isMemberPage()) return false;
    // صفحة البوابة (تحتوي بطاقة Provider) ليست صفحة دخول
    if (providerCard(bridge) || memberLink(bridge)) return false;
    return !!document.querySelector('input[type="password"]');
  }

  // ------------------------------------------------------------------
  // عناصر الدخول (معرّفات البوابة غير موثّقة — نح heuristically)
  // ------------------------------------------------------------------
  function passwordField(bridge) {
    return (
      [...document.querySelectorAll('input[type="password"]')].find((el) =>
        visible(el, bridge)
      ) || null
    );
  }

  function usernameField(bridge) {
    const explicit = document.querySelector(
      'input[name="username"], input#username, input[id*="username" i], input[name*="username" i],' +
        ' input[placeholder*="username" i], input[aria-label*="username" i], input[id*="user" i]'
    );
    if (explicit && visible(explicit, bridge)) return explicit;

    // حقل النص الأقرب لكلمة المرور (داخل نفس النموذج)
    const pass = passwordField(bridge);
    const form = pass && (pass.form || pass.closest("form"));
    if (form) {
      const cand = [...form.querySelectorAll('input[type="text"], input:not([type])')].find(
        (el) => visible(el, bridge) && el !== pass
      );
      if (cand) return cand;
    }

    // أي حقل نصي ظاهر ليس من حقولنا المعروفة
    return (
      [...document.querySelectorAll('input[type="text"], input:not([type])')].find(
        (el) =>
          visible(el, bridge) &&
          !el.disabled &&
          !el.readOnly &&
          el.id !== "CaptchaInputText" &&
          el.id !== "txtMemberId" &&
          el.id !== "txtGsmNo"
      ) || null
    );
  }

  function submitControl(bridge) {
    const vis = (sel) =>
      [...document.querySelectorAll(sel)].filter((el) => visible(el, bridge) && !el.disabled);

    // 1) زر إرسال حقيقي داخل نموذج (أضمن طريقة لإرسال النموذج)
    const real = vis('input[type="submit"], button[type="submit"], button:not([type])').find(
      (el) => el.form || el.closest("form")
    );
    if (real) return real;

    // 2) زر بنص معروف
    const byLabel = vis('button, input[type="submit"], input[type="button"], a.btn').find((el) =>
      /log\s*in|sign\s*in|submit|connect|دخول/i.test(text(el))
    );
    if (byLabel) return byLabel;

    // 3) أي زر داخل نموذج كلمة المرور
    const pass = passwordField(bridge);
    const form = pass && (pass.form || pass.closest("form"));
    if (form) {
      const b = [...form.querySelectorAll('button, input[type="submit"]')].find((el) =>
        visible(el, bridge)
      );
      if (b) return b;
    }
    return null;
  }

  // ------------------------------------------------------------------
  // روابط التنقل
  // ------------------------------------------------------------------
  // بطاقة "Provider" في لوحة البوابة. البوابة إما Angular (`.card-title`
  // يحوي span نصّه Provider) أو بوابة FortiGate الكلاسيكية (رابط/زر).
  function providerTitle(bridge) {
    const isProvider = (el) => /^provider$/i.test(text(el));

    const titles = [
      ...document.querySelectorAll(
        ".card-title, .card h3, .card h4, [class*='card-title'], [class*='cardTitle']"
      ),
    ];
    const hit = titles.find((el) => isProvider(el) && visible(el, bridge));
    if (hit) return hit;

    const candidates = [
      ...document.querySelectorAll("a, button, [role='button'], li, [class*='bookmark']"),
    ];
    return candidates.find((el) => isProvider(el) && visible(el, bridge)) || null;
  }

  // نُعيد أعمق عنصر يحمل النص: النقر عليه يصعد الحدث إلى كل الآباء
  // (span → card-title → card) فأي مستوى فيه handler يستجيب.
  function providerCard(bridge) {
    const title = providerTitle(bridge);
    if (!title) return null;

    // رابط حقيقي (بوابة كلاسيكية)؟ نقر أصلي يفتح التبويب بنفسه
    const a = title.tagName === "A" ? title : title.querySelector("a[href]");
    if (a) return a;

    const deep = [...title.querySelectorAll("*")].filter(
      (el) => /^provider$/i.test(text(el)) && visible(el, bridge)
    );
    return deep.length ? deep[deep.length - 1] : title;
  }

  // فتح بوابة المزوّد من بطاقة Provider.
  // الموقع يفتحها في تبويب/نافذة جديدة عبر window.open — والمتصفح يحظر
  // النوافذ الآلية (لا تفاعل مستخدم حقيقي)، فالنقرة "تنجح" بلا أي نتيجة.
  // الحل: نلتقط الرابط الذي أراده الموقع ونُبحر إليه في نفس التبويب.
  async function openProvider(bridge) {
    const title = providerTitle(bridge);
    const cardEl = title && (title.closest(".card") || title.parentElement);
    const icon = title && title.querySelector("nu-icon, svg, i");

    const targets = [];
    const deepest = providerCard(bridge);
    if (deepest) targets.push(deepest);
    if (cardEl && cardEl !== deepest) targets.push(cardEl);
    if (icon && icon !== deepest) targets.push(icon);

    for (const el of targets) {
      const r = await bridge.captureNav({
        click: () => clickLikeUser(el),
        timeout: 4000,
      });
      if (r.ok) {
        try {
          location.href = r.url;
        } catch (e) { /* ignore */ }
        return { ok: true, url: r.url, via: "captured" };
      }
      // نقرة بلا window.open: ربما رابط حقيقي فتح بنفسه — نتحقق من العنوان
      if (location.href !== START_HREF) return { ok: true, url: location.href, via: "navigation" };
    }

    // رابط عادي داخل البطاقة (بوابة كلاسيكية)
    const a = title && (title.closest("a[href]") || title.querySelector("a[href]"));
    if (a && a.href) {
      location.href = a.href;
      return { ok: true, url: a.href, via: "anchor" };
    }
    return { ok: false, error: "provider_open_failed" };
  }

  function memberLink(bridge) {
    const links = [...document.querySelectorAll("a")].filter((el) => visible(el, bridge));
    return (
      links.find(
        (a) =>
          /member\s*eligib/i.test(text(a)) ||
          /MemberDetails/i.test(a.getAttribute("href") || "")
      ) || null
    );
  }

  // ------------------------------------------------------------------
  // اختيار الطبيب — قائمة jQuery UI (ul.ui-autocomplete)
  // ------------------------------------------------------------------
  function doctorItems() {
    const out = [];
    for (const li of document.querySelectorAll("ul.ui-autocomplete li.ui-menu-item")) {
      const w = li.querySelector(".ui-menu-item-wrapper") || li;
      const t = text(w);
      if (t) out.push({ el: li, target: w, text: t });
    }
    return out;
  }

  // مرشحو حقل الطبيب — بالترتيب من الأدقّ:
  // 1) الحقل المربوط فعلاً بقائمة jQuery UI (كلاس ui-autocomplete-input /
  //    role=combobox) — يسجّله jQuery UI لحظة التهيئة.
  // 2) حقل اسمه doctor/consultant/physician.
  // 3) تسمية نصية بجوار الحقل، ثم أي حقل نصي آخر كملاذ أخير.
  function doctorCandidates(bridge) {
    const out = [];
    // حقول نعرف أنها ليست حقل الطبيب (تُستبعد حتى لو حملت كلاس القائمة)
    const NOT_DOCTOR = new Set([
      "txtMemberId",
      "txtGsmNo",
      "CaptchaInputText",
      "txtSpeciality", // ui-autocomplete-input لكنها حقل الاختصاص — تُملأ تلقائياً
    ]);
    const push = (el) => {
      if (el && NOT_DOCTOR.has(el.id)) return;
      if (el && visible(el, bridge) && !el.disabled && !el.readOnly && !out.includes(el)) {
        out.push(el);
      }
    };

    for (const el of document.querySelectorAll("input.ui-autocomplete-input, input[role='combobox']")) {
      push(el);
    }

    for (const el of document.querySelectorAll(
      'input[id*="doctor" i], input[name*="doctor" i], input[placeholder*="doctor" i],' +
        ' input[id*="consultant" i], input[name*="consultant" i],' +
        ' input[id*="physician" i], input[name*="physician" i]'
    )) {
      push(el);
    }

    // تسمية نصية بجوار الحقل
    for (const el of document.querySelectorAll("label, span, td, th")) {
      if (!/doctor|physician|consultant/i.test(el.textContent || "")) continue;
      if (el.children.length > 2) continue;
      const forId = el.getAttribute && el.getAttribute("for");
      const inp = (forId && document.getElementById(forId)) || el.querySelector("input");
      if (inp) push(inp);
    }

    // أي حقل نصي آخر ظاهر (ملاذ أخير فقط)
    const known = new Set(["txtMemberId", "txtGsmNo", "CaptchaInputText"]);
    for (const el of document.querySelectorAll('input[type="text"], input:not([type])')) {
      if (known.has(el.id)) continue;
      push(el);
    }
    return out;
  }

  function normName(s) {
    return String(s || "")
      .toUpperCase()
      .replace(/\bDR\b\.?/g, " ")
      .replace(/[^A-Z0-9 ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function matchDoctor(items, wanted) {
    const w = normName(wanted);
    if (!w) return null;
    // وزن كلمتين: تطابق تام 1، بادئة (مثل "N" ≈ "NITHIN") 0.5 — سجلات
    // HIS قد تختصر الاسم الأول لأحرفه الأولى والبوابة يكتبه كاملاً.
    const wordScore = (a, b) => {
      if (a === b) return 1;
      return b.startsWith(a) || a.startsWith(b) ? 0.5 : 0;
    };
    let best = null;
    let bestScore = 0;
    for (const it of items) {
      const n = normName(it.text);
      if (!n) continue;
      let score = 0;
      if (n === w) score = 100;
      else if (n.includes(w) || w.includes(n)) score = 80;
      else {
        const a = w.split(" ").filter(Boolean);
        const b = n.split(" ").filter(Boolean);
        let weighted = 0;
        for (const x of a) {
          for (const y of b) {
            if (!wordScore(x, y)) continue;
            weighted += wordScore(x, y);
            break;
          }
        }
        score = a.size ? Math.round((weighted / a.size) * 70) : 0;
      }
      if (score > bestScore) {
        bestScore = score;
        best = it;
      }
    }
    return bestScore >= 50 ? best : null;
  }

  // اسم الطبيب كما يُكتب في حقل البحث — بلا "Dr." ولا علامات (المطابقة
  // بالاسم الخام أدق: بعض الأسماء بلا DR. والموقع يقارن حساساً)
  function cleanDoctorName(s) {
    return String(s || "")
      .replace(/[.,]/g, " ")
      .replace(/\bdr\b/i, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  async function selectDoctor(card, bridge) {
    const wanted = String(card.visit?.consultant || "").trim();
    const typeText = cleanDoctorName(wanted);

    // نكتب الاسم حقلاً بعد حقل حتى تستجيب واحدة بقائمة jQuery UI —
    // وكل محاولة بتنويعين: الاسم كاملاً ثم أول 3 أحرف (بعض المصادر
    // تبحث بنص قصير فقط). بلا اسم في HIS: نلتقط قائمة مفتوحة مسبقاً فقط.
    let usedInput = null;
    let items = [];
    const variants = typeText ? [typeText, typeText.slice(0, 3)] : [""];
    for (const inp of doctorCandidates(bridge).slice(0, typeText ? 8 : 3)) {
      for (const variant of variants) {
        if (variant) {
          inp.focus();
          bridge.setNativeValue(inp, variant);
          typeLetter(inp); // يفتح قائمة jQuery UI
        }
        const got = await bridge.waitFor(
          () => {
            const l = doctorItems();
            return l.length ? l : null;
          },
          { timeout: 2500, poll: 200 }
        );
        if (got) {
          items = got;
          usedInput = inp;
          break;
        }
        // حقل بلا قائمة → نظّفه وجرّب التالي
        try { inp.value = ""; } catch (e) { /* ignore */ }
      }
      if (usedInput) break;
    }

    const cands = doctorCandidates(bridge)
      .map((i) => `#${i.id || i.name || "?"}`)
      .slice(0, 8)
      .join(",");

    if (!items.length) {
      bridge.progress(
        "eligibility",
        "error",
        `doctor_list_empty typed="${typeText}" fields=${cands}`
      );
      bridge.overlay.error("Doctor list did not appear — showing candidates…");
      dumpPage(bridge);
      bridge.probe();
      bridge.fail("doctor_list_empty");
    }
    bridge.progress(
      "eligibility",
      "run",
      `doctor list ready items=${items.length} input=${usedInput ? usedInput.id || usedInput.name : "?"}`
    );

    let target = wanted ? matchDoctor(items, wanted) : null;
    if (!target) {
      // لا مطابقة (أو لا يوجد طبيب في HIS) → نسأل الموظف من صفحة المشفى
      const options = items.map((it) => it.text);
      const answer = await bridge.ask({
        kind: "choice",
        title: "Liva — choose the doctor",
        options,
        hint: wanted ? `HIS doctor: ${wanted}` : "The HIS record has no doctor name",
      });
      const pick = answer?.value ? items.find((it) => it.text === answer.value) : null;
      if (pick) {
        target = pick;
      } else if (!wanted && items.length) {
        target = items[0];
        bridge.progress("eligibility", "run", "doctor=first_item (no consultant in HIS)");
      } else {
        bridge.progress("eligibility", "error", `doctor_not_found | ${options.join(" / ")}`);
        bridge.fail("doctor_not_selected");
      }
    }

    // الاختيار بالنقر (القائمة إجبارية — الكتابة وحدها لا تكفي) ثم نتأكد أن
    // الحقل أخذ نصّ العنصر، وإلا نثبّته بأنفسنا.
    clickLikeUser(target.target);
    const confirm = await bridge.waitFor(
      () => {
        const v = String((usedInput && usedInput.value) || "").trim();
        return normName(v) === normName(target.text) ? v : null;
      },
      { timeout: 3000, poll: 200 }
    );
    if (!confirm && usedInput) {
      bridge.setNativeValue(usedInput, target.text);
    }
    return target.text;
  }

  // ------------------------------------------------------------------
  // الكابتشا
  // ------------------------------------------------------------------
  async function captchaImage(bridge) {
    const img = document.querySelector(SEL.captchaImg);
    if (!img) return null;

    // 1) نفس الأصل — نقرأ بكسلات الصورة مباشرة (بلا طلب إضافي للسيرفر)
    try {
      await bridge.waitFor(() => img.complete && img.naturalWidth > 0, {
        timeout: 5000,
        poll: 200,
      });
      if (img.naturalWidth > 0) {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth;
        c.height = img.naturalHeight;
        c.getContext("2d").drawImage(img, 0, 0);
        return c.toDataURL("image/png");
      }
    } catch (e) { /* نتابع للطريقة الثانية */ }

    // 2) جلب مباشر مع كوكيز الجلسة
    try {
      const res = await fetch(img.src, { credentials: "include", cache: "no-store" });
      const blob = await res.blob();
      return await blobToDataUrl(blob);
    } catch (e) {
      return null;
    }
  }

  // ------------------------------------------------------------------
  // مراحل الصفحة
  // ------------------------------------------------------------------
  function stateLabel(bridge) {
    return [
      `url=${location.pathname}${location.search}`.slice(0, 90),
      `title="${document.title.slice(0, 30)}"`,
      `vpn=${isVpnLogin(bridge) ? 1 : 0}`,
      `prov=${isProviderLogin() ? 1 : 0}`,
      `member=${document.querySelector(SEL.member) ? 1 : 0}`,
      `mv="${String(document.querySelector(SEL.member)?.value || "").trim()}"`,
      `doctor=${doctorItems().length}`,
      `link=${memberLink(bridge) ? 1 : 0}`,
      `card=${providerCard(bridge) ? 1 : 0}`,
      `print=${printButton(bridge) ? 1 : 0}`,
    ].join(" ");
  }

  // البوابة تُبنى بـAngular: عند document_idle قد يكون الـDOM ما زال فارغاً.
  // ننتظر ظهور إشارة حقيقية قبل الحكم على الصفحة (لا مهلة عشوائية).
  async function waitForKnownPage(bridge, timeout = 25000) {
    return await bridge.waitFor(
      () => {
        if (isProviderLogin()) return "provider-login";
        if (isMemberPage()) return "member";
        if (providerCard(bridge)) return "portal-card";
        if (memberLink(bridge)) return "portal-link";
        if (passwordField(bridge)) return "vpn-login";
        return null;
      },
      { timeout, poll: 400 }
    );
  }

  // صفحة غير معروفة → نسجّل ما فيها فعلاً في سجل الإضافة (لا تخمين)
  function dumpPage(bridge) {
    if (!bridge.diag) return;
    const t = (el) => String(el.textContent || "").replace(/\s+/g, " ").trim();
    const iframes = [...document.querySelectorAll("iframe")].map((f) => f.src || f.name || "?");
    bridge.diag(
      "liva",
      `page title="${document.title.slice(0, 60)}" path=${location.pathname} ready=${document.readyState} body=${document.body ? document.body.children.length : 0}`
    );
    bridge.diag("liva", `iframes=${iframes.length ? iframes.slice(0, 5).join(" | ") : "-"}`);

    const seen = new Set();
    for (const el of document.querySelectorAll(
      "a, button, [role='button'], .card-title, [class*='card'], [class*='bookmark'], h1, h2, h3"
    )) {
      if (!visible(el, bridge)) continue;
      const txt = t(el).slice(0, 40);
      if (!txt) continue;
      const href = (el.getAttribute && el.getAttribute("href")) || "";
      const cls = String(el.className || "")
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .join(".");
      seen.add(
        `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}${href ? " href=" + href.slice(0, 60) : ""} "${txt}"`
      );
    }
    const list = [...seen].slice(0, 40);
    if (!list.length) bridge.diag("liva", "no clickable candidates");
    const joined = list.join(" ; ");
    for (let i = 0; i < joined.length; i += 340) {
      bridge.diag("liva", joined.slice(i, i + 340));
    }
  }

  function printButton(bridge) {
    const explicit = document.querySelector(SEL.printBtn);
    if (explicit && visible(explicit, bridge)) return explicit;
    return byText(
      "button, a.btn, input[type=submit]",
      /print\s+online\s+claim|print\s+claim/i,
      bridge
    );
  }

  function normalizeGsm(v) {
    let d = String(v || "").replace(/\D/g, "");
    if (d.length > 8 && d.startsWith("968")) d = d.slice(3);
    if (d.length > 8) d = d.slice(-8);
    return d;
  }

  async function doVpnLogin(creds, bridge) {
    bridge.overlay.show("Liva");
    if (!creds.user || !creds.pass) {
      bridge.progress("login", "error", "no_credentials");
      bridge.overlay.error("Liva credentials missing — open extension settings");
      bridge.fail("no_credentials");
    }
    const attempt = bump(VPN_KEY);
    if (attempt > 3) {
      bridge.progress("login", "error", `vpn_login_failed | ${stateLabel(bridge)}`);
      bridge.overlay.error("VPN login failed 3 times — check the credentials");
      bridge.fail("vpn_login_failed");
    }

    bridge.overlay.step("Signing in to the VPN portal…", "run");
    bridge.progress("login", "run", `vpn attempt=${attempt}`);

    const user = usernameField(bridge);
    const pass = passwordField(bridge);
    if (!user || !pass) {
      bridge.progress("login", "error", `login_fields_not_found | ${stateLabel(bridge)}`);
      bridge.overlay.error("Login fields not found — showing candidates…");
      bridge.probe();
      bridge.fail("login_fields_not_found");
    }
    bridge.setNativeValue(user, creds.user);
    bridge.setNativeValue(pass, creds.pass);

    const submit = submitControl(bridge);
    if (!submit) {
      bridge.progress("login", "error", "submit_not_found");
      bridge.overlay.error("Login button not found — showing candidates…");
      bridge.probe();
      bridge.fail("submit_not_found");
    }
    clickLikeUser(submit);
    bridge.progress("login", "ok", "vpn");
  }

  async function doProviderLogin(creds, bridge) {
    bridge.overlay.show("Liva");
    if (!creds.user || !creds.pass) {
      bridge.progress("login", "error", "no_credentials");
      bridge.overlay.error("Liva credentials missing — open extension settings");
      bridge.fail("no_credentials");
    }
    const attempt = bump(PROVIDER_KEY);
    if (attempt > 4) {
      bridge.progress("login", "error", `provider_login_failed | ${stateLabel(bridge)}`);
      bridge.overlay.error("Provider login failed 4 times");
      bridge.fail("provider_login_failed");
    }

    const user = usernameField(bridge);
    const pass = passwordField(bridge);
    if (!user || !pass) {
      bridge.progress("login", "error", `login_fields_not_found | ${stateLabel(bridge)}`);
      bridge.overlay.error("Login fields not found — showing candidates…");
      bridge.probe();
      bridge.fail("login_fields_not_found");
    }
    if (String(user.value || "").trim() !== creds.user) bridge.setNativeValue(user, creds.user);
    if (String(pass.value || "") !== creds.pass) bridge.setNativeValue(pass, creds.pass);

    // ---- الكابتشا: تُعرض في صفحة المشفى ----
    const cap = document.querySelector(SEL.captcha);
    let code = String(cap.value || "").trim();
    if (!code) {
      bridge.overlay.step("Waiting for the captcha…", "run");
      bridge.progress("captcha", "run", "solving captcha");
      const image = await captchaImage(bridge);
      const answer = await bridge.ask({
        kind: "captcha",
        title: "Liva — type the captcha",
        image,
        placeholder: "Captcha code",
        hint: "Type the characters shown in the image",
      });
      code = String(answer?.value || "").trim();
      if (!code && answer?.error === "no_his_tab") {
        // بلا صفحة مشفى → الموظف يكتب الكابتشا في نافذة البوابة (مكشوفة)
        bridge.progress("captcha", "run", "manual fallback");
        code =
          (await bridge.waitFor(
            () => {
              const v = String(document.querySelector(SEL.captcha)?.value || "").trim();
              return v.length >= 3 ? v : null;
            },
            { timeout: 180000, poll: 500 }
          )) || "";
      }
    }
    if (!code) {
      bridge.progress("captcha", "error", "captcha_missing");
      bridge.overlay.error("Captcha was not provided");
      bridge.fail("captcha_missing");
    }

    bridge.setNativeValue(cap, code);
    const submit = submitControl(bridge);
    if (!submit) {
      bridge.progress("login", "error", "submit_not_found");
      bridge.overlay.error("Login button not found — showing candidates…");
      bridge.probe();
      bridge.fail("submit_not_found");
    }
    clickLikeUser(submit);
    try { sessionStorage.setItem(SUBMIT_KEY, String(Date.now())); } catch (e) { /* ignore */ }
    bridge.progress("login", "ok", "provider");
  }

  async function doMemberPage(card, bridge) {
    bridge.overlay.show("Liva");
    const memberNo = String(card.insurance?.card_no || "").trim();
    if (!memberNo) {
      bridge.progress("eligibility", "error", "member_number_missing");
      bridge.overlay.error("No insurance number on the patient record");
      bridge.fail("member_number_missing");
    }

    // ---- 1) رقم العضوية + زر Search (#btnSearch) ----
    // Enter لا يشغّل البحث حسب الاختبار الحيوي — الزر هو الطريقة الرسمية.
    // البحث قد يُعيد تحميل الصفحة — نتذكّر رقم العضوية الذي بُحث عنه حتى
    // لا نبحث مرتين.
    const member = document.querySelector(SEL.member);
    const searchedFor = sessionStorage.getItem(SEARCH_KEY) || "";
    if (searchedFor !== memberNo) {
      if (String(member.value || "").trim() !== memberNo) {
        bridge.overlay.step(`Entering member no ${memberNo}…`, "run");
        bridge.setNativeValue(member, memberNo);
      }
      sessionStorage.setItem(SEARCH_KEY, memberNo);
      const searchBtn =
        document.querySelector("#btnSearch") ||
        byText("input[type=button], button", /^\s*search\s*$/i, bridge);
      if (searchBtn) {
        clickLikeUser(searchBtn);
        bridge.progress("submitted", "ok", `member=${memberNo} via Search`);
      } else {
        bridge.diag("liva", "search button #btnSearch not found — falling back to Enter");
        pressEnter(member);
        bridge.progress("submitted", "ok", `member=${memberNo} via Enter`);
      }
      // ننتظر استعدادات الحقول بعد البحث (إشارة: حقل الطبيب ظاهر ومفعّل)
      await bridge.waitFor(
        () => {
          const di = doctorCandidates(bridge)[0];
          return di ? di : null;
        },
        { timeout: 12000, poll: 300 }
      );
    }

    // ---- 2) اختيار الطبيب (قائمة jQuery UI — لا كتابة فقط) ----
    bridge.overlay.step("Selecting the doctor…", "run");
    const doctor = await selectDoctor(card, bridge);

    // ---- 3) الهاتف (رقم محلي بلا رمز الدولة — قناع الموقع يضيف 968-) ----
    // الدرس من اللوج الحقيقي: أحداث النقر الاصطناعية لا تحرّك المؤشر
    // (caret) في Chrome، فكتب القناع في مواضع خطأ: العرض يأكل أول رقمين
    // والمستند يطبع بلا رقم — رغم أن value يبدو سليماً. الطريقة التي
    // نجحت يدك = مؤشر فعلي على أول خانة (بعد 968-) + كتابة رقم-رقم.
    // setSelectionRange واجهة حقيقية تحرّك المؤشر فعلاً حتى من
    // سكربت المحتوى — هي الأساس، والنقر مجرد استكمال.
    const gsm = normalizeGsm(card.patient?.phone);
    const gsmInput = document.querySelector(SEL.gsm);
    if (gsmInput && gsm) {
      // شرط النجاح صارم: العرض = "968-" + الرقم كاملاً — لا .includes
      // (كانت تعتبر كتابة القناع الملتوية "نجاحاً" فلا نصل للطريقة الصحيحة)
      const fieldState = () => {
        const raw = String(gsmInput.value || "");
        return { raw, ok: raw === `968-${gsm}` };
      };
      const typeDigits = () => {
        for (const ch of String(gsm)) {
          try {
            const ev = { key: ch, code: "Digit" + ch, bubbles: true, cancelable: true };
            gsmInput.dispatchEvent(new KeyboardEvent("keydown", { ...ev }));
            if (typeof document.execCommand === "function") {
              document.execCommand("insertText", false, ch);
            }
            gsmInput.dispatchEvent(new KeyboardEvent("keyup", { ...ev }));
          } catch (e) { /* ignore */ }
        }
      };
      // موضع أول خانة قابلة للكتابة (بعد بادئة القناع إن وُجدت)
      const gsmCaret = () => {
        const base = String(gsmInput.value || gsmInput.placeholder || "");
        return /^968-/.test(base) ? 4 : 0;
      };

      let st = fieldState();
      let via = st.ok ? "prefilled" : "";

      // 0) عبر قناع الموقع نفسه (عالم الصفحة + jQuery inputmask) —
      // الضمان الوحيد لتطابق مخزن القناع الداخلي مع ما يصل للسيرفر
      // (المستنان طُبع بلا رقم لكل المسارات من خارج القناع).
      if (!st.ok && typeof bridge.pageCmd === "function") {
        try {
          const r = await bridge.pageCmd("gsmSet", [gsm], 2500);
          if (r?.ok && String(r.val) === `968-${gsm}`) {
            st = { raw: String(r.val), ok: true };
            via = "mask";
          } else {
            bridge.progress(
              "eligibility", "run",
              `gsm mask_try val="${(r && r.val) || ""}" mask=${!!(r && r.mask)} err="${(r && r.err) || ""}"`
            );
          }
        } catch (e) { /* القناة غير متاحة */ }
      }

      // أ) مؤشر فعلي على أول خانة + كتابة رقم-رقم (طريقة اليد 1:1)
      if (!st.ok) {
        gsmInput.focus();
        try { gsmInput.setSelectionRange(gsmCaret(), gsmCaret()); } catch (e) { /* ignore */ }
        for (const t of ["mousedown", "mouseup", "click"]) {
          try { gsmInput.dispatchEvent(new MouseEvent(t, { bubbles: true })); } catch (e) { /* ignore */ }
        }
        try { gsmInput.setSelectionRange(gsmCaret(), gsmCaret()); } catch (e) { /* ignore */ }
        typeDigits();
        st = fieldState();
        if (!st.ok) {
          // القناع ردّ كتابتنا — تفريغ بحدوده هو قبل إعادة المحاولة
          try { gsmInput.setSelectionRange(0, String(gsmInput.value || "").length); } catch (e) { /* ignore */ }
          if (typeof document.execCommand === "function") document.execCommand("delete");
        }
        if (st.ok) via = "caret4";
      }

      // ب) النقر فقط (الموظف تنقل المؤشر بفاعلية نظرياً عبر mask focus)
      if (!st.ok) {
        gsmInput.focus();
        for (const t of ["mousedown", "mouseup", "click"]) {
          try { gsmInput.dispatchEvent(new MouseEvent(t, { bubbles: true })); } catch (e) { /* ignore */ }
        }
        try { gsmInput.setSelectionRange(gsmCaret(), gsmCaret()); } catch (e) { /* ignore */ }
        typeDigits();
        st = fieldState();
        if (st.ok) via = "click";
      }

      // ج) مؤشر نهاية النص (End) + كتابة
      if (!st.ok) {
        try { gsmInput.setSelectionRange(String(gsmInput.value || "").length, String(gsmInput.value || "").length); } catch (e) { /* ignore */ }
        fireKey(gsmInput, "End", "End", 35);
        typeDigits();
        st = fieldState();
        if (st.ok) via = "end";
      }

      // د) الاحتياط النهائي: قيمة خام بلا أي حدث إدخال — العرض والقيمة
      // يظلان صحيحين (968-98208159). لا نلمس الحقل بعدها إطلاقاً.
      if (!st.ok) {
        try { gsmInput.value = `968-${gsm}`; } catch (e) { /* ignore */ }
        st = fieldState();
        via = st.ok ? "raw" : "";
      }
      bridge.progress(
        "eligibility",
        "run",
        st.ok ? `gsm=${gsm} ok via=${via} field="${st.raw}"` : `gsm=${gsm} MISMATCH field="${st.raw}"`
      );
    }

    // ---- 4) التعبئة التلقائية بعد اختيار الطبيب ----
    // #txtSpeciality تُملأ بـAJAX بعد اختيار الطبيب مباشرة — الطباعة قبل
    // امتلائها تعطي استمارة ناقصة. إشارة: الحقل non-empty.
    const specInput = document.querySelector("#txtSpeciality");
    if (specInput) {
      bridge.overlay.step("Waiting for auto-fill (speciality)…", "run");
      const spec = await bridge.waitFor(
        () => {
          const v = String(specInput.value || "").trim();
          return v ? v : null;
        },
        { timeout: 14000, poll: 300 }
      );
      if (spec) {
        bridge.progress("eligibility", "run", `speciality=${spec}`);
      } else {
        bridge.progress("eligibility", "error", "speciality_autofill_timeout");
        bridge.overlay.error("The speciality field did not auto-fill — please check the form");
        bridge.fail("speciality_autofill_missing");
      }
    } else {
      // غياب الحقل نفسه معلومة — لا صمت (كان هذا يخفينا التشخيص)
      bridge.progress("eligibility", "run", "speciality field_absent in DOM — skipping guard");
    }

    // ---- 5) الطباعة ----
    const btn = printButton(bridge);
    if (!btn) {
      bridge.progress("eligibility", "error", `print_button_not_found | ${stateLabel(bridge)}`);
      bridge.overlay.error("Print button not found — showing candidates…");
      bridge.probe();
      bridge.fail("print_button_not_found");
    }
    bridge.overlay.step("Sending the print order…", "run");
    bridge.progress("print", "run", `click doctor=${doctor}`);
    const r = await bridge.printReport({
      click: () => printButton(bridge) || btn,
      urlRe: REPORT_RE_SRC,
      timeout: 30000,
    });
    if (!r.ok) {
      bridge.progress("print", "error", r.error || "print_failed");
      bridge.overlay.error("Could not capture the report — showing candidates…");
      bridge.probe();
      bridge.fail(r.error || "print_failed");
    }
    if (r.url) bridge.overlay.step("Report captured — printing in your window", "ok");
    // بلا رابط مباشر؟ التسليح عبر webRequest يجري في الخلفية — النتيجة
    // الحاسمة (طباعة/فشل) تصل عبر خطوات progress من الخلفية.
  }

  // ------------------------------------------------------------------
  // المحوّل
  // ------------------------------------------------------------------
  let busy = false;

  window.__InsurerAdapter = {
    id: "liva",

    async run(msg, bridge) {
      // الخلفية تُعيد الإرسال بعد كل تنقل — نمنع تشغيلين متزامنين
      // (فتح تبويب البوابة مرتين لو نقرتان على البطاقة).
      if (busy) {
        bridge.progress("adapter", "run", "already running — duplicate dispatch ignored");
        return;
      }
      busy = true;
      try {
        const { card, creds } = msg;

        // البوابة Angular — ننتظر ظهور إشارة حقيقية بدل الحكم على DOM فارغ
        const seen = await waitForKnownPage(bridge);

        if (!seen) {
          bridge.overlay.show("Liva");
          bridge.progress("adapter", "error", `unknown_page | ${stateLabel(bridge)}`);
          dumpPage(bridge);
          bridge.overlay.error("Could not recognise the Liva page — showing candidates…");
          bridge.probe();
          bridge.fail("unknown_page");
        }

        // 1) بوابة المزوّد (كابتشا) — قبل فحص بوابة VPN (لأنها فيها password)
        if (seen === "provider-login") {
          // الرول: لا نطلب كابتشا جديدة إلا بعد استقرار الصفحة وبقاء حقل
          // الكابتشا ظاهراً — صفحة البوابة الانتقالية بعد دخول ناجح تحمل
          // نفس الحقول لحظات. اللحظة تصلنا من الخلفية (msg.submitAt) لأن
          // sessionStorage عبر الصفحات غير مضمون.
          const sub = Math.max(
            Number(msg.submitAt || 0),
            Number(sessionStorage.getItem(SUBMIT_KEY) || 0)
          );
          if (sub && Date.now() - sub < 20000) {
            bridge.overlay.step("Finishing the provider sign-in…", "run");
            bridge.progress("login", "run", "provider waiting for result");
            // إشارة النجاح: اختفاء حقل الكابتشا (تنقّل إلى لوحة المزوّد)
            const outcome = await bridge.waitFor(
              () => (isProviderLogin() ? null : "signed-in"),
              { timeout: Math.max(2000, 21000 - (Date.now() - sub)), poll: 400 }
            );
            // عدّاد المحاولات يُصفَّر عند النجاح — صلاحيته داخل مهمة واحدة فقط
            try { sessionStorage.setItem(PROVIDER_KEY, "0"); } catch (e) { /* ignore */ }
            if (!outcome && isProviderLogin()) {
              // فشل فعلي: نكرر الدخول بإعادة كتابة البيانات وكابتشا جديدة
              await doProviderLogin(creds, bridge);
            }
            return;
          }
          await doProviderLogin(creds, bridge);
          return;
        }

        // 2) صفحة تفاصيل العضوية
        if (seen === "member") {
          await doMemberPage(card, bridge);
          return;
        }

        // 3) بوابة VPN
        if (seen === "vpn-login") {
          await doVpnLogin(creds, bridge);
          return;
        }

        // تجاوزنا البوابة → عدّاد دخول VPN خُدِم (يُصفَّر للمهمة التالية)
        try { sessionStorage.setItem(VPN_KEY, "0"); } catch (e) { /* ignore */ }

        // 4) رابط "Member Eligiblity & Online Claim Form"
        if (seen === "portal-link") {
          bridge.overlay.show("Liva");
          bridge.overlay.step("Opening Member Eligibility…", "run");
          bridge.progress("navigate", "run", "click=Member Eligibility");
          clickLikeUser(memberLink(bridge));
          return;
        }

        // 5) بطاقة "Provider" (تفتح بوابة المزوّد في تبويب جديد)
        bridge.overlay.show("Liva");
        bridge.overlay.step("Opening the provider portal…", "run");
        const opened = await openProvider(bridge);
        if (!opened.ok) {
          bridge.progress("navigate", "error", `provider_open_failed | ${stateLabel(bridge)}`);
          dumpPage(bridge);
          bridge.overlay.error("Could not open the provider portal — showing candidates…");
          bridge.probe();
          bridge.fail(opened.error || "provider_open_failed");
        }
        bridge.progress("navigate", "ok", `provider via ${opened.via} → ${String(opened.url || "").slice(0, 110)}`);
      } finally {
        busy = false;
      }
    },
  };
})();
