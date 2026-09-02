/* MaxCallBridge — طبقة الجسر بين الواجهة و C++ عبر WebView2 */
'use strict';

/**
 * يرسل رسالة إلى C++ بأمان (WebView2 أو fallback للتصفح العادي).
 * @param {object} msg رسالة بصيغة {action, ...}
 * @returns {boolean} true إذا أُرسلت عبر WebView2
 */
function _postToNative(msg) {
  try {
    if (window.chrome && window.chrome.webview && typeof window.chrome.webview.postMessage === 'function') {
      window.chrome.webview.postMessage(msg);
      return true;
    }
  } catch (e) {
    console.warn('[MaxCallBridge] postMessage failed:', e);
  }
  console.log('[MaxCallBridge:fallback]', JSON.stringify(msg));
  return false;
}

/**
 * يبث حدث DOM داخلي لتحديث الواجهة.
 * @param {string} name اسم الحدث
 * @param {object} detail بيانات الحدث
 */
function _emit(name, detail) {
  window.dispatchEvent(new CustomEvent('maxcall:' + name, { detail }));
  console.log('[MaxCallBridge:rx]', name, detail || {});
}

/**
 * جسر MaxCall: إرسال JS->C++ واستقبال C++->JS.
 */
const MaxCallBridge = {
  // ---------- إرسال: JS -> C++ ----------

  /**
   * بدء مكالمة صادرة.
   * @param {{number: string}} p
   */
  makeCall(p) { _postToNative({ action: 'makeCall', number: String((p && p.number) || '') }); },

  /**
   * إنهاء المكالمة الحالية.
   * @param {{callId?: number}} p
   */
  hangup(p) { _postToNative({ action: 'hangup', callId: (p && p.callId) ?? -1 }); },

  /**
   * الرد على مكالمة واردة.
   * @param {{callId?: number}} p
   */
  answer(p) { _postToNative({ action: 'answer', callId: (p && p.callId) ?? -1 }); },

  /**
   * تعليق / استئناف المكالمة.
   * @param {{callId?: number, on: boolean}} p
   */
  hold(p) { _postToNative({ action: 'hold', callId: (p && p.callId) ?? -1, on: !!(p && p.on) }); },

  /**
   * تحويل المكالمة لرقم آخر.
   * @param {{callId?: number, target: string}} p
   */
  transfer(p) { _postToNative({ action: 'transfer', callId: (p && p.callId) ?? -1, target: String((p && p.target) || '') }); },

  /**
   * إرسال نغمة DTMF.
   * @param {{callId?: number, digit: string}} p رقم واحد 0-9/*#
   */
  sendDTMF(p) { _postToNative({ action: 'sendDTMF', callId: (p && p.callId) ?? -1, digit: String((p && p.digit) || '') }); },

  /**
   * تغيير حالة الحضور.
   * @param {{status: 'available'|'busy'|'away'|'offline'}} p
   */
  setPresence(p) { _postToNative({ action: 'setPresence', status: String((p && p.status) || 'available') }); },

  // ---------- استقبال: C++ -> JS ----------

  /**
   * مكالمة واردة جديدة (يستدعيها C++ عبر ExecuteScript).
   * @param {string} number رقم المتصل
   * @param {string} name اسم المتصل
   * @param {number} callId معرف المكالمة
   */
  onIncomingCall(number, name, callId) {
    const banner = document.getElementById('incomingBanner');
    const numEl = document.getElementById('incomingNumber');
    if (banner) banner.classList.remove('hidden');
    if (numEl) numEl.textContent = (name ? name + ' • ' : '') + number;
    _emit('incoming-call', { number, name, callId });
  },

  /**
   * تغيّر حالة المكالمة.
   * @param {number} callId معرف المكالمة
   * @param {string} state إحدى: idle|calling|ringing|active|held|ended
   */
  onCallState(callId, state) {
    const label = document.getElementById('callState');
    const map = { idle: 'جاهز للاتصال', calling: 'جارٍ الاتصال…', ringing: 'يرن…', active: 'مكالمة نشطة', held: 'المكالمة معلّقة', ended: 'انتهت المكالمة' };
    if (label) label.textContent = map[state] || state;
    if (state === 'ended') document.getElementById('incomingBanner')?.classList.add('hidden');
    _emit('call-state', { callId, state });
  },

  /**
   * تغيّر حالة التسجيل في المقسم.
   * @param {boolean} registered هل الحساب مسجّل؟
   * @param {string} message رسالة وصفية
   */
  onRegState(registered, message) {
    const dot = document.getElementById('regDot');
    const txt = document.getElementById('regText');
    if (dot) dot.className = 'h-2.5 w-2.5 rounded-full ' + (registered ? 'bg-emerald-400' : 'bg-rose-400');
    if (txt) txt.textContent = registered ? 'مسجّل' : 'غير مسجّل';
    _emit('reg-state', { registered, message });
  },

  /**
   * رسالة نصية / تنبيه من C++.
   * @param {string} from المرسل
   * @param {string} text نص الرسالة
   */
  onMessage(from, text) { _emit('message', { from, text }); },
};

// دوال عالمية يستدعيها C++ مباشرة عبر ExecuteScript (نفس أسلوب updateCallerInfo)
window.onIncomingCall = (...a) => MaxCallBridge.onIncomingCall(...a);
window.onCallState = (...a) => MaxCallBridge.onCallState(...a);
window.onRegState = (...a) => MaxCallBridge.onRegState(...a);
window.onMessage = (...a) => MaxCallBridge.onMessage(...a);
window.MaxCallBridge = MaxCallBridge;
