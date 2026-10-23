'use strict';

/**
 * Send a message to C++ via WebView2. Falls back to console.log outside WebView2.
 * @param {object} msg Message object with an `action` field.
 * @returns {boolean} True when delivered through WebView2.
 */
function _postToNative(msg) {
  // NOTE: always send a JSON string (not an object) — C++ reads the
  // message with TryGetWebMessageAsString, same as the proven CRM popup.
  let text = null;
  try { text = JSON.stringify(msg); } catch (err) { console.warn('[MaxCallBridge] stringify failed:', err); return false; }
  try {
    if (window.chrome && window.chrome.webview && typeof window.chrome.webview.postMessage === 'function') {
      window.chrome.webview.postMessage(text);
      return true;
    }
  } catch (err) {
    console.warn('[MaxCallBridge] postMessage failed:', err);
  }
  console.log('[MaxCallBridge:fallback]', text);
  return false;
}

/**
 * Broadcast an internal DOM event for UI updates.
 * @param {string} name Event short name (suffixed to `maxcall:`).
 * @param {object} [detail] Event payload.
 */
function _emit(name, detail) {
  window.dispatchEvent(new CustomEvent('maxcall:' + name, { detail: detail || {} }));
}

/** @type {number} Active call id, -1 when idle. */
let _currentCallId = -1;
/** @type {boolean} Whether a call is currently in progress. */
let _inCall = false;
/** @type {number} Timestamp (ms) when the active state started. */
let _activeSince = 0;
/** @type {number|null} Interval id for the duration counter. */
let _timerId = null;
/** @type {boolean} Whether the first onRegState has arrived (hides the engine banner). */
let _gotFirstReg = false;
/** @type {number} Last auto-update state pushed by C++ (0 idle, 1 checking, 2 available, 3 downloading, 4 verifying, 5 ready, 6 restarted, 7 error). */
let _updateState = 0;
/** @type {boolean} Whether the user hid the update banner for this session. */
let _updateDismissed = false;
/** @type {Array<{name:string,number:string,presence:string}>} Cached contacts. */
let _contacts = [];
/** @type {Map<number,{number:string,name:string,direction:string,wasActive:boolean}>} Per-call tracking for log typing. */
const _callMeta = new Map();
/** @type {string} Last dialed number (marks direction=out on ended). */
let _lastDialed = '';

/**
 * Format seconds as m:ss.
 * @param {number} secs Elapsed seconds.
 * @returns {string} Formatted duration.
 */
function _fmtDur(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return m + ':' + String(s).padStart(2, '0');
}

/** Refresh the duration counter display. */
function _tickTimer() {
  const el = document.getElementById('acTimer');
  if (!el || !_activeSince) return;
  el.textContent = _fmtDur((Date.now() - _activeSince) / 1000);
}

/** Start the JS-side duration counter. */
function _startTimer() {
  _activeSince = Date.now();
  _tickTimer();
  if (_timerId !== null) clearInterval(_timerId);
  _timerId = setInterval(_tickTimer, 500);
}

/** Hide the incoming-call banner. */
function _hideIncoming() {
  document.getElementById('incomingBanner')?.classList.add('hidden');
  document.getElementById('incomingBanner')?.classList.remove('flex');
}

/** Show the outgoing-call banner with a number. */
function _showOutgoing(number, name) {
  const banner = document.getElementById('outgoingBanner');
  const numEl = document.getElementById('outgoingNumber');
  const nameEl = document.getElementById('outgoingName');
  if (numEl) numEl.textContent = String(number || '');
  if (nameEl) nameEl.textContent = String(name || '');
  if (banner) { banner.classList.remove('hidden'); banner.classList.add('flex'); }
}

/** Hide the outgoing-call banner. */
function _hideOutgoing() {
  document.getElementById('outgoingBanner')?.classList.add('hidden');
  document.getElementById('outgoingBanner')?.classList.remove('flex');
}

/** Show the snoozed-incoming mini strip. */
function _showSnooze(number, name) {
  const strip = document.getElementById('incomingSnooze');
  const txt = document.getElementById('incomingSnoozeText');
  if (txt) {
    const num = String(number || '');
    txt.textContent = 'Incoming call ' + num + ' — tap to answer';
  }
  if (strip) { strip.classList.remove('hidden'); strip.classList.add('flex'); }
  void name;
}

/** Hide the snoozed-incoming mini strip. */
function _hideSnooze() {
  document.getElementById('incomingSnooze')?.classList.add('hidden');
  document.getElementById('incomingSnooze')?.classList.remove('flex');
}

/** Restore the full incoming banner (from snooze). */
function _restoreIncoming() {
  const banner = document.getElementById('incomingBanner');
  if (banner) { banner.classList.remove('hidden'); banner.classList.add('flex'); }
  _hideSnooze();
}

/** Stop the JS-side duration counter. */
function _stopTimer() {
  if (_timerId !== null) { clearInterval(_timerId); _timerId = null; }
  _activeSince = 0;
  const el = document.getElementById('acTimer');
  if (el) el.textContent = '0:00';
}

/** @type {number} Last processed call id for dedupe. */
let _lastStateCall = -2;
/** @type {string} Last processed state for dedupe. */
let _lastState = '';

/** Pause icon for the hold button. */
const HOLD_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4"><path d="M9 5v14M15 5v14"/></svg>';
/** Play icon for the resume button. */
const RESUME_ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4"><path d="m6 4 14 8-14 8V4Z"/></svg>';
/** Slanted arrow icons for the call log (Tailwind colors inherited from badge). */
const LOG_ICON_MISSED = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4"><path d="M17 7 7 17M16 17H7V8"/></svg>';
const LOG_ICON_IN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4"><path d="M7 7l10 10M17 8v9H8"/></svg>';
const LOG_ICON_OUT = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-4 w-4"><path d="M7 17 17 7M8 7h9v9"/></svg>';

/**
 * Update the hold button icon and label.
 * @param {boolean} held True when the call is held (show Resume).
 * @returns {void}
 */
function _setHoldUI(held) {
  const b = document.getElementById('btnHold');
  if (!b) return;
  b.title = held ? 'Resume' : 'Hold';
  b.setAttribute('aria-label', held ? 'Resume' : 'Hold');
  b.innerHTML = held ? RESUME_ICON : HOLD_ICON;
}

/**
 * Color tones for the update banner: [banner background, title color, message color].
 */
const UPDATE_TONES = {
  info: ['bg-emerald-50', 'text-emerald-900', 'text-emerald-700'],
  error: ['bg-rose-50', 'text-rose-900', 'text-rose-700'],
};

/** Show or hide the update banner row. */
function _showUpdateBanner(show) {
  const banner = document.getElementById('updateBanner');
  if (!banner) return;
  banner.classList.toggle('hidden', !show);
  banner.classList.toggle('flex', !!show);
}

/** Repaint the update banner with one of the UPDATE_TONES palettes. */
function _setUpdateTone(name) {
  const palette = UPDATE_TONES[name] || UPDATE_TONES.info;
  const targets = ['updateBanner', 'updateVersion', 'updateMessage'];
  targets.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    Object.values(UPDATE_TONES).forEach((tone) => el.classList.toggle(tone[i], tone[i] === palette[i]));
  });
}

/** Enable or disable every update-banner button. */
function _setUpdateButtonsDisabled(disabled) {
  ['btnUpdateNow', 'btnUpdateRetry', 'btnUpdateLater'].forEach((id) => {
    const b = document.getElementById(id);
    if (b) b.disabled = !!disabled;
  });
}

/**
 * The MaxCall bridge: senders (JS->C++) and receivers (C++->JS).
 */
const MaxCallBridge = {
  // ---------- Senders: JS -> C++ (via window.chrome.webview.postMessage) ----------

  /**
   * Notify C++ that the shell is ready; C++ replies with a full snapshot.
   * @returns {void}
   */
  shellReady() { _postToNative({ action: 'shellReady' }); },

  /**
   * Request a data snapshot (used by the Retry button).
   * @returns {void}
   */
  getData() { _postToNative({ action: 'getData' }); },

  /**
   * Start an outgoing call.
   * @param {string} number Destination number.
   * @returns {void}
   */
  makeCall(number) {
    const num = String(number || '').trim();
    if (!num) return;
    _lastDialed = num;
    _postToNative({ action: 'makeCall', number: num });
  },

  /**
   * Hang up a call.
   * @param {number} [callId] Call id, -1 = current.
   * @returns {void}
   */
  hangup(callId) { _postToNative({ action: 'hangup', callId: callId ?? _currentCallId }); },

  /**
   * Answer an incoming call.
   * @param {number} [callId] Call id, -1 = current.
   * @returns {void}
   */
  answer(callId) { _postToNative({ action: 'answer', callId: callId ?? _currentCallId }); },

  /**
   * Hold or resume a call.
   * @param {number} [callId] Call id, -1 = current.
   * @param {boolean} on True = hold, false = resume.
   * @returns {void}
   */
  hold(callId, on) { _postToNative({ action: 'hold', callId: callId ?? _currentCallId, on: !!on }); },

  /**
   * Blind-transfer a call to another target.
   * @param {number} [callId] Call id, -1 = current.
   * @param {string} target Transfer destination.
   * @returns {void}
   */
  transfer(callId, target) {
    _postToNative({ action: 'transfer', callId: callId ?? _currentCallId, target: String(target || '').trim() });
  },

  /**
   * Send a DTMF digit on a call.
   * @param {number} [callId] Call id, -1 = current.
   * @param {string} digit Single digit 0-9 * #.
   * @returns {void}
   */
  sendDTMF(callId, digit) {
    _postToNative({ action: 'sendDTMF', callId: callId ?? _currentCallId, digit: String(digit || '') });
  },

  /**
   * Change presence status.
   * @param {string} status One of available|busy|away|offline.
   * @returns {void}
   */
  setPresence(status) { _postToNative({ action: 'setPresence', status: String(status || 'available') }); },

  /**
   * Ask C++ to open the settings dialog.
   * @returns {void}
   */
  openSettings() { _postToNative({ action: 'openSettings' }); },

  /**
   * Ask C++ to quit the app.
   * @returns {void}
   */
  quitApp() { _postToNative({ action: 'quitApp' }); },

  /**
   * Ask C++ to minimize the app window.
   * @returns {void}
   */
  minimizeApp() { _postToNative({ action: 'minimizeApp' }); },

  /**
   * Ask C++ to open the caller-details popup for a number.
   * @param {string} number Phone number.
   * @returns {void}
   */
  openPopup(number) {
    const num = String(number || '').trim();
    if (!num) return;
    _postToNative({ action: 'openPopup', number: num });
  },

  /**
   * Toggle always-on-top pinning.
   * @param {boolean} on True = pin on top.
   * @returns {void}
   */
  pinToggle(on) { _postToNative({ action: 'pinToggle', on: !!on }); },

  /**
   * Ask C++ to check for updates right now.
   * @returns {void}
   */
  checkUpdate() { _postToNative({ action: 'checkUpdate' }); },

  /**
   * Ask C++ to download and install the pending update.
   * @returns {void}
   */
  installUpdate() { _postToNative({ action: 'installUpdate' }); },

  /**
   * Hide the update banner for this session. C++ keeps its own state untouched,
   * so the next onUpdateState brings the banner back.
   * @returns {void}
   */
  dismissUpdate() {
    _updateDismissed = true;
    _showUpdateBanner(false);
    _postToNative({ action: 'dismissUpdate' });
    _emit('update-dismissed', { state: _updateState, dismissed: true });
  },

  /**
   * Request the feedback task queue from C++ (via the bridge → server).
   * @returns {void}
   */
  feedbackQueue() { _postToNative({ action: 'feedbackQueue' }); },

  /**
   * Request this agent's claimed (in-progress) feedback tasks from C++.
   * @returns {void}
   */
  feedbackMy() { _postToNative({ action: 'feedbackMy' }); },

  /**
   * Claim a feedback task.
   * @param {number|string} taskId Task id.
   * @returns {void}
   */
  feedbackClaim(taskId) { _postToNative({ action: 'feedbackClaim', taskId: String(taskId) }); },

  /**
   * Respond to a feedback task.
   * @param {number|string} taskId Task id.
   * @param {string} outcome One of done|no_answer|refused|wrong_number|callback_later|not_applicable.
   * @param {object} [fields] Extra fields (score, categories, comment, duration_sec, next_attempt_at).
   * @returns {void}
   */
  feedbackRespond(taskId, outcome, fields = {}) {
    _postToNative({ action: 'feedbackRespond', taskId: String(taskId), outcome, ...fields });
  },

  /**
   * Skip a feedback task.
   * @param {number|string} taskId Task id.
   * @returns {void}
   */
  feedbackSkip(taskId) { _postToNative({ action: 'feedbackSkip', taskId: String(taskId) }); },

  // ---------- Receivers: C++ -> JS (called via ExecuteScript) ----------

  /**
   * Registration state changed.
   * @param {boolean} registered Whether the account is registered.
   * @param {string} message Human-readable detail.
   * @returns {void}
   */
  onRegState(registered, message) {
    const dot = document.getElementById('regDot');
    const txt = document.getElementById('regText');
    if (dot) dot.className = 'h-2 w-2 rounded-full ' + (registered ? 'bg-emerald-400' : 'bg-rose-400');
    if (txt) txt.textContent = registered ? 'Registered' : 'Offline';
    if (!_gotFirstReg) {
      _gotFirstReg = true;
      document.getElementById('engineBanner')?.classList.add('hidden');
    }
    _emit('reg-state', { registered: !!registered, message: String(message || '') });
  },

  /**
   * Call state changed.
   * @param {number} callId Call id.
   * @param {string} state One of idle|calling|ringing|active|held|ended.
   * @param {string} [number] Remote number.
   * @param {string} [name] Remote name.
   * @returns {void}
   */
  onCallState(callId, state, number, name) {
    const num = String(number || '');
    const nm = String(name || '');
    _currentCallId = Number(callId ?? _currentCallId);

    if (!_callMeta.has(_currentCallId)) {
      const direction = _lastDialed && num && _lastDialed === num ? 'out' : (_lastDialed ? 'out' : 'in');
      _callMeta.set(_currentCallId, { number: num, name: nm, direction, wasActive: false });
    }
    const meta = _callMeta.get(_currentCallId);
    if (num) meta.number = num;
    if (nm) meta.name = nm;

    // Dedupe: PJSUA repeats identical transitions; ignore exact repeats.
    if (_currentCallId === _lastStateCall && state === _lastState) return;
    _lastStateCall = _currentCallId;
    _lastState = state;

    const bar = document.getElementById('activeCallBar');
    const acNum = document.getElementById('acNumber');
    const acName = document.getElementById('acName');
    const acState = document.getElementById('acState');
    if (acNum) acNum.textContent = meta.number || num || '—';
    if (acName) acName.textContent = meta.name || nm || 'Unknown';
    if (acState) acState.textContent = state;

    if (state === 'active') {
      meta.wasActive = true;
      _inCall = true;
      bar?.classList.remove('hidden');
      _setHoldUI(false);
      _hideIncoming();
      _hideOutgoing();
      _hideSnooze();
      _startTimer();
    } else if (state === 'held') {
      _inCall = true;
      bar?.classList.remove('hidden');
      _setHoldUI(true);
      _hideOutgoing();
    } else if (state === 'calling') {
      _inCall = true;
      bar?.classList.add('hidden');
      _hideIncoming();
      _showOutgoing(meta.number || num, meta.name || nm);
    } else if (state === 'ringing') {
      _inCall = true;
      if (meta.direction === 'out') {
        bar?.classList.add('hidden');
        _hideIncoming();
        _showOutgoing(meta.number || num, meta.name || nm);
      } else {
        bar?.classList.add('hidden');
        _hideOutgoing();
      }
    } else if (state === 'ended' || state === 'idle') {
      _inCall = false;
      _stopTimer();
      bar?.classList.add('hidden');
      _hideIncoming();
      _hideOutgoing();
      _hideSnooze();
      if (state === 'ended') _appendLog(meta);
      if (!_inCall) { _currentCallId = -1; _lastDialed = ''; }
      _callMeta.delete(Number(callId));
    }
    _emit('call-state', { callId: _currentCallId, state, number: meta.number, name: meta.name });
  },

  /**
   * New incoming call.
   * @param {string} number Caller number.
   * @param {string} name Caller name.
   * @param {number} callId Call id.
   * @returns {void}
   */
  onIncomingCall(number, name, callId) {
    _currentCallId = Number(callId);
    _callMeta.set(_currentCallId, { number: String(number || ''), name: String(name || ''), direction: 'in', wasActive: false });
    const banner = document.getElementById('incomingBanner');
    const numEl = document.getElementById('incomingNumber');
    const nameEl = document.getElementById('incomingName');
    if (numEl) numEl.textContent = String(number || '');
    if (nameEl) nameEl.textContent = String(name || '');
    _hideOutgoing();
    _hideSnooze();
    if (banner) { banner.classList.remove('hidden'); banner.classList.add('flex'); }
    _emit('incoming-call', { number: String(number || ''), name: String(name || ''), callId: _currentCallId });
  },

  /**
   * Text message / alert from C++.
   * @param {string} from Sender.
   * @param {string} text Message text.
   * @returns {void}
   */
  onMessage(from, text) {
    const toast = document.getElementById('msgToast');
    const body = document.getElementById('msgText');
    if (body) body.textContent = (from ? from + ': ' : '') + String(text || '');
    if (toast) {
      toast.classList.remove('hidden');
      clearTimeout(toast._hideT);
      toast._hideT = setTimeout(() => toast.classList.add('hidden'), 3500);
    }
    _emit('message', { from: String(from || ''), text: String(text || '') });
  },

  /**
   * Full contact list snapshot.
   * @param {string} jsonText JSON array string: [{name,number,presence}].
   * @returns {void}
   */
  onContacts(jsonText) {
    let arr = [];
    try { arr = JSON.parse(String(jsonText || '[]')); } catch { arr = []; }
    if (!Array.isArray(arr)) arr = [];
    _contacts = arr.map((c) => ({
      name: String(c && c.name || ''),
      number: String(c && c.number || ''),
      presence: String(c && c.presence || ''),
    }));
    _renderContacts(document.getElementById('contactSearch')?.value || '');
    _emit('contacts', { contacts: _contacts });
  },

  /**
   * Account identity.
   * @param {string} user SIP user.
   * @param {string} domain SIP domain.
   * @param {string} [name] Display name (optional).
   * @returns {void}
   */
  onAccount(user, domain, name) {
    const el = document.getElementById('accountLine');
    if (el) {
      const raw = String(user || '').split('@')[0];
      const disp = String(name || raw || '');
      el.textContent = raw ? raw + '-' + (disp || raw) : 'not signed in';
    }
    _emit('account', { user: String(user || ''), domain: String(domain || ''), name: String(name || '') });
  },

  /**
   * Always-on-top pin state (sent in every snapshot).
   * @param {boolean} on Whether the window is pinned.
   * @returns {void}
   */
  onPinState(on) {
    const btn = document.getElementById('btnPin');
    if (btn) {
      btn.classList.toggle('bg-gold', !!on);
      btn.classList.toggle('text-teal-950', !!on);
      btn.classList.toggle('text-white/70', !on);
    }
    _emit('pin-state', { on: !!on });
  },

  /**
   * Auto-update state pushed by C++.
   * @param {number} state 0 idle | 1 checking | 2 available | 3 downloading | 4 verifying | 5 ready | 6 restarted | 7 error.
   * @param {string} version Available version, may be empty.
   * @param {number} percent Download progress 0..100 (state 3 only).
   * @param {string} message Ready-to-display English text, may be empty.
   * @returns {void}
   */
  onUpdateState(state, version, percent, message) {
    const st = Number(state) || 0;
    const ver = String(version || '');
    const msg = String(message || '');
    const pct = Math.max(0, Math.min(100, Number(percent) || 0));
    const wasDismissed = _updateDismissed;
    _updateState = st;
    _updateDismissed = false;

    const verEl = document.getElementById('updateVersion');
    const msgEl = document.getElementById('updateMessage');
    const prog = document.getElementById('updateProgress');
    const bar = document.getElementById('updateProgressBar');
    const btnNow = document.getElementById('btnUpdateNow');
    const btnRetry = document.getElementById('btnUpdateRetry');

    // The bar is the only inline-styled node: Tailwind cannot express a ratio.
    const progress = (on) => {
      if (prog) prog.classList.toggle('hidden', !on);
      if (!on || !bar) return;
      bar.style.width = pct + '%';
      prog?.setAttribute('aria-valuenow', String(Math.round(pct)));
    };

    // No default heading here: writing "Update available" before the state is
    // known made a failed check look like a real update offer.
    progress(st === 3);
    _setUpdateTone(st === 7 ? 'error' : 'info');

    if (st === 0 || st === 6) {
      _showUpdateBanner(false);
      _setUpdateButtonsDisabled(false);
    } else if (st === 1) {
      // Keep the banner hidden during a silent background check; only refresh it if already open.
      if (verEl) verEl.textContent = 'Checking…';
      if (msgEl) msgEl.textContent = 'Contacting the update server…';
      _setUpdateButtonsDisabled(true);
      _showUpdateBanner(!document.getElementById('updateBanner')?.classList.contains('hidden'));
    } else if (st === 2) {
      if (verEl) verEl.textContent = ver ? 'Update ' + ver : 'Update available';
      if (msgEl) msgEl.textContent = msg || 'A new version is ready to install';
      btnNow?.classList.remove('hidden');
      btnNow?.classList.add('flex');
      btnRetry?.classList.add('hidden');
      _setUpdateButtonsDisabled(false);
      _showUpdateBanner(true);
    } else if (st === 3 || st === 4) {
      if (verEl) verEl.textContent = ver ? 'Update ' + ver : 'Update';
      if (msgEl) msgEl.textContent = st === 4 ? 'Verifying…' : (msg || 'Downloading… ' + Math.round(pct) + '%');
      btnNow?.classList.remove('hidden');
      btnNow?.classList.add('flex');
      btnRetry?.classList.add('hidden');
      _setUpdateButtonsDisabled(true);
      _showUpdateBanner(true);
    } else if (st === 5) {
      if (verEl) verEl.textContent = ver ? 'MaxCall ' + ver : 'MaxCall';
      if (msgEl) msgEl.textContent = 'Restarting MaxCall…';
      btnNow?.classList.add('hidden');
      btnNow?.classList.remove('flex');
      btnRetry?.classList.add('hidden');
      _setUpdateButtonsDisabled(true);
      _showUpdateBanner(true);
    } else if (st === 7) {
      if (verEl) verEl.textContent = 'Update check failed';
      if (msgEl) msgEl.textContent = msg || 'MaxCall could not reach the update server.';
      btnNow?.classList.add('hidden');
      btnNow?.classList.remove('flex');
      btnRetry?.classList.remove('hidden');
      btnRetry?.classList.add('flex');
      _setUpdateButtonsDisabled(false);
      _showUpdateBanner(true);
    } else {
      _showUpdateBanner(false);
    }

    _emit('update-state', { state: st, version: ver, percent: pct, message: msg, wasDismissed });
  },

  /**
   * Full call-history snapshot (newest first as received). Replaces logList content.
   * @param {string} jsonText JSON array string: [{time,number,name,direction,status,duration,answered_by,has_rec}].
   * @returns {void}
   */
  onCallHistory(jsonText) {
    let arr = [];
    try {
      const parsed = JSON.parse(String(jsonText || '[]'));
      // السيرفر يرسل {rows:[...]} — ونقبل المصفوفة المباشرة أيضاً
      arr = Array.isArray(parsed) ? parsed : (parsed && parsed.rows) || [];
    } catch { arr = []; }
    if (!Array.isArray(arr)) arr = [];
    const list = document.getElementById('logList');
    const empty = document.getElementById('logEmpty');
    if (!list) return;
    list.innerHTML = '';
    arr.forEach((it) => {
      const number = String((it && it.number) || '');
      const name = String((it && it.name) || '');
      const time = String((it && it.time) || '');
      const status = String((it && it.status) || '');
      const answeredBy = String((it && it.answered_by) || '');
      const dur = Number((it && it.duration) || 0);
      void ((it && it.direction) || '');
      void (it && it.has_rec);
      const durSuffix = dur > 0 ? ' • ' + _fmtDur(dur) : '';
      let type;
      let label;
      if (status === 'answered-by') {
        type = 'answered-by';
        label = answeredBy ? 'Answered by ' + answeredBy : 'Answered by';
      } else if (status === 'missed') {
        type = 'missed';
        label = 'Missed';
      } else if (status === 'outgoing') {
        type = 'outgoing';
        label = 'Outgoing';
      } else if (status === 'outgoing-no-answer') {
        type = 'outgoing-no-answer';
        label = 'No answer';
      } else if (status === 'called-back-answered') {
        type = 'called-back-answered';
        label = 'Called back \u2713';
      } else if (status === 'called-back-no-answer') {
        type = 'called-back-no-answer';
        label = 'Called back \u2717';
      } else {
        type = 'in';
        label = 'Incoming';
      }
      const row = _buildLogRow({
        number,
        name,
        label,
        time: time + durSuffix,
        type,
        withRedial: true,
      });
      list.appendChild(row);
    });
    if (empty) empty.classList.toggle('hidden', arr.length > 0);
    if (typeof _applyLogFilter === 'function') _applyLogFilter();
    _emit('call-history', { count: arr.length });
  },
};

/**
 * Render the contact list, filtered by a query.
 * @param {string} query Search text.
 * @returns {void}
 */
function _renderContacts(query) {
  const list = document.getElementById('contactList');
  const empty = document.getElementById('contactEmpty');
  const count = document.getElementById('contactCount');
  if (!list) return;
  const q = String(query || '').toLowerCase().trim();
  const shown = _contacts.filter((c) =>
    !q || c.name.toLowerCase().includes(q) || c.number.replace(/\s/g, '').includes(q.replace(/\s/g, '')));
  if (count) count.textContent = String(shown.length);
  list.innerHTML = '';
  shown.forEach((c) => {
    const row = document.createElement('div');
    row.className = 'flex items-center justify-between bg-white p-2.5';
    const dot = c.presence === 'busy' ? 'bg-rose-400' : c.presence === 'away' ? 'bg-amber-400' : 'bg-emerald-400';
    row.innerHTML =
      '<div class="flex min-w-0 items-center gap-2">' +
      '<span class="relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-50 text-xs font-bold text-teal-800">' +
      '<span></span>' +
      '<span class="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-white ' + dot + '"></span></div>' +
      '<div class="min-w-0"><p class="truncate text-xs font-bold select-text"></p><p class="text-[11px] text-slate-400 select-text" dir="ltr"></p></div></div>' +
      '<div class="flex shrink-0 items-center gap-1">' +
      '<button data-act="info" title="Caller details" aria-label="Caller details" class="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-slate-500 active:scale-95"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg></button>' +
      '<button data-act="call" title="Call" aria-label="Call" class="flex h-7 w-7 items-center justify-center rounded-full bg-teal-700/10 text-teal-800 active:scale-95"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/></svg></button></div>';
    row.querySelector('span span').textContent = (c.name || c.number || '?').charAt(0).toUpperCase();
    row.querySelector('p').textContent = c.name || c.number;
    row.querySelectorAll('p')[1].textContent = c.number;
    row.querySelector('[data-act="call"]').addEventListener('click', () => {
      MaxCallBridge.makeCall(c.number);
      _switchTab('tabDial');
    });
    row.querySelector('[data-act="info"]').addEventListener('click', () => MaxCallBridge.openPopup(c.number));
    list.appendChild(row);
  });
  if (empty) empty.classList.toggle('hidden', shown.length > 0);
}

/**
 * Build a call-log row element (shared by live log and server history).
 * @param {{number:string,name:string,label:string,time:string,type:string,withRedial:boolean}} opts Row data.
 * @returns {HTMLDivElement} Row element.
 */
function _buildLogRow(opts) {
  const o = opts || {};
  const number = String(o.number || '');
  const name = String(o.name || '');
  const label = String(o.label || '');
  const time = String(o.time || '');
  const type = String(o.type || '');
  const withRedial = o.withRedial !== false;
  let badge;
  let icon;
  if (type === 'missed') {
    badge = 'bg-rose-50 text-rose-600';
    icon = LOG_ICON_MISSED;
  } else if (type === 'answered-by') {
    badge = 'bg-teal-50 text-teal-600';
    icon = LOG_ICON_IN;
  } else if (type === 'no-answer' || type === 'outgoing-no-answer' || type === 'called-back-no-answer') {
    badge = 'bg-amber-50 text-amber-600';
    icon = LOG_ICON_OUT;
  } else if (type === 'out' || type === 'outgoing') {
    badge = 'bg-sky-50 text-sky-600';
    icon = LOG_ICON_OUT;
  } else if (type === 'called-back-answered') {
    badge = 'bg-emerald-50 text-emerald-600';
    icon = LOG_ICON_OUT;
  } else {
    badge = 'bg-emerald-50 text-emerald-600';
    icon = LOG_ICON_IN;
  }
  const row = document.createElement('div');
  row.className = 'flex items-center justify-between rounded-2xl border border-slate-100 bg-white p-2.5';
  row.innerHTML =
    '<div class="flex min-w-0 items-center gap-2">' +
    '<span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full ' + badge + '">' + icon + '</span>' +
    '<div class="min-w-0"><p class="truncate text-xs font-bold select-text" dir="ltr"></p>' +
    '<p class="truncate text-[11px] text-slate-400 select-text"></p></div></div>' +
    '<div class="flex shrink-0 items-center gap-1.5">' +
    '<span class="rounded-full px-2 py-0.5 text-[11px] font-bold ' + badge + '">' + label + '</span>' +
    '<button data-act="info" title="Caller details" aria-label="Caller details" class="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-slate-500 active:scale-95"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg></button>' +
    (withRedial ? '<button data-act="redial" title="Redial" aria-label="Redial" class="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-slate-600 active:scale-95"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/></svg></button>' : '') + '</div>';
  row.querySelectorAll('p')[0].textContent = number || name || 'Unknown';
  row.querySelectorAll('p')[1].textContent = (name || number || 'Unknown') + ' • ' + time;
  row.querySelector('[data-act="redial"]')?.addEventListener('click', () => {
    const input = document.getElementById('dialInput');
    if (input) input.value = number;
    MaxCallBridge.makeCall(number);
    _switchTab('tabDial');
  });
  row.querySelector('[data-act="info"]').addEventListener('click', () => MaxCallBridge.openPopup(number));
  return row;
}

/**
 * Append one row to the JS-side call log.
 * @param {{number:string,name:string,direction:string,wasActive:boolean}} meta Call metadata.
 * @returns {void}
 */
function _appendLog(meta) {
  const list = document.getElementById('logList');
  const empty = document.getElementById('logEmpty');
  if (!list) return;
  const type = meta.direction === 'in' && !meta.wasActive ? 'missed' : meta.direction;
  const time = new Date().toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const label = type === 'missed' ? 'Missed' : type === 'in' ? 'Incoming' : 'Outgoing';
  const row = _buildLogRow({
    number: meta.number || '',
    name: meta.name || '',
    label,
    time: time + ' • ' + type,
    type,
    withRedial: true,
  });
  list.prepend(row);
  if (empty) empty.classList.add('hidden');
  _applyLogFilter();
}

/**
 * Filter visible log rows by number/name query.
 * @param {string} query Search text.
 * @returns {void}
 */
function _applyLogFilter(query) {
  const list = document.getElementById('logList');
  if (!list) return;
  const q = String(query === undefined ? document.getElementById('logSearch')?.value || '' : query).toLowerCase().trim();
  const norm = q.replace(/\s/g, '');
  list.querySelectorAll(':scope > div').forEach((row) => {
    const text = (row.textContent || '').toLowerCase();
    const compact = text.replace(/\s/g, '');
    row.classList.toggle('hidden', !(!q || text.includes(q) || (norm && compact.includes(norm))));
  });
}

/**
 * Switch visible tab panel.
 * @param {string} id Panel id: tabDial|tabContacts|tabLog.
 * @returns {void}
 */
function _switchTab(id) {
  ['tabDial', 'tabContacts', 'tabLog', 'tabFeedback'].forEach((t) => document.getElementById(t)?.classList.add('hidden'));
  document.getElementById(id)?.classList.remove('hidden');
  document.querySelectorAll('.tab-btn').forEach((b) => {
    const active = b.dataset.tab === id;
    b.classList.toggle('bg-brand', active);
    b.classList.toggle('text-white', active);
    b.classList.toggle('font-bold', active);
    b.classList.toggle('font-medium', !active);
    b.classList.toggle('text-slate-400', !active);
  });
  if (id === 'tabDial') document.getElementById('dialInput')?.focus();
  else if (id === 'tabContacts') document.getElementById('contactSearch')?.focus();
  else if (id === 'tabLog') document.getElementById('logSearch')?.focus();
  else if (id === 'tabFeedback') { _fetchFeedbackQueue(); _fetchFeedbackMy(); }
}

/**
 * Switch between the New and My panes inside the Tasks section.
 * @param {string} which 'new' or 'my'.
 * @returns {void}
 */
function _switchFeedbackSub(which) {
  const isNew = which !== 'my';
  [['fbSubNew', true], ['fbSubMy', false]].forEach(([id, cond]) => {
    const b = document.getElementById(id);
    if (!b) return;
    const on = cond === isNew;
    b.classList.toggle('shadow-sm', on);
    b.classList.toggle('font-bold', on);
    b.classList.toggle('text-slate-800', on);
    b.classList.toggle('font-medium', !on);
    b.classList.toggle('text-slate-400', !on);
  });
  document.getElementById('fbPaneNew')?.classList.toggle('hidden', !isNew);
  document.getElementById('fbPaneMy')?.classList.toggle('hidden', isNew);
}

/** Wire up all DOM controls. */
function _wireUI() {
  document.addEventListener('contextmenu', (e) => e.preventDefault());

  document.querySelectorAll('.tab-btn').forEach((b) =>
    b.addEventListener('click', () => _switchTab(b.dataset.tab)));

  const input = document.getElementById('dialInput');
  let zeroTimer = null;
  let zeroLong = false;
  const zeroBtn = document.querySelector('[data-digit="0"]');
  function zeroStart() {
    zeroLong = false;
    if (zeroTimer !== null) clearTimeout(zeroTimer);
    zeroTimer = setTimeout(() => {
      zeroLong = true;
      if (_inCall) MaxCallBridge.sendDTMF(undefined, '+');
      else if (input) input.value += '+';
    }, 500);
  }
  function zeroCancel() {
    if (zeroTimer !== null) { clearTimeout(zeroTimer); zeroTimer = null; }
  }
  if (zeroBtn) {
    zeroBtn.addEventListener('mousedown', zeroStart);
    zeroBtn.addEventListener('mouseup', zeroCancel);
    zeroBtn.addEventListener('mouseleave', zeroCancel);
    zeroBtn.addEventListener('touchstart', zeroStart, { passive: true });
    zeroBtn.addEventListener('touchend', zeroCancel);
  }
  document.getElementById('dialPad')?.addEventListener('click', (e) => {
    const b = e.target.closest('[data-digit]');
    if (!b || !input) return;
    const d = b.dataset.digit;
    if (d === '0' && zeroLong) { zeroLong = false; return; }
    if (_inCall) MaxCallBridge.sendDTMF(undefined, d);
    else input.value += d;
  });
  document.getElementById('btnBackspace')?.addEventListener('click', () => {
    if (input) input.value = input.value.slice(0, -1);
  });

  document.getElementById('btnCall')?.addEventListener('click', () =>
    MaxCallBridge.makeCall(input?.value.trim()));
  document.getElementById('btnHangup')?.addEventListener('click', () => MaxCallBridge.hangup(undefined));
  document.getElementById('btnPopup')?.addEventListener('click', () =>
    MaxCallBridge.openPopup(input?.value.trim() || _callMeta.get(_currentCallId)?.number));
  document.getElementById('btnPin')?.addEventListener('click', () => {
    const pinned = document.getElementById('btnPin')?.classList.contains('bg-gold');
    MaxCallBridge.pinToggle(!pinned);
  });
  document.getElementById('btnHold')?.addEventListener('click', () => {
    const holding = document.getElementById('btnHold')?.title === 'Hold';
    MaxCallBridge.hold(undefined, holding);
  });
  document.getElementById('btnTransfer')?.addEventListener('click', () =>
    MaxCallBridge.transfer(undefined, document.getElementById('transferTarget')?.value));

  document.getElementById('btnIncomingAnswer')?.addEventListener('click', () => MaxCallBridge.answer(undefined));
  document.getElementById('btnIncomingReject')?.addEventListener('click', () => {
    const meta = _callMeta.get(_currentCallId);
    _hideIncoming();
    _showSnooze(meta?.number || document.getElementById('incomingNumber')?.textContent || '', meta?.name || '');
  });
  document.getElementById('incomingSnooze')?.addEventListener('click', _restoreIncoming);
  document.getElementById('btnOutgoingCancel')?.addEventListener('click', () => MaxCallBridge.hangup(undefined));

  document.getElementById('btnMinimize')?.addEventListener('click', () => MaxCallBridge.minimizeApp());
  document.getElementById('btnQuit')?.addEventListener('click', () => MaxCallBridge.quitApp());
  document.getElementById('btnRetry')?.addEventListener('click', () => MaxCallBridge.getData());

  document.getElementById('btnUpdateNow')?.addEventListener('click', () => MaxCallBridge.installUpdate());
  document.getElementById('btnUpdateLater')?.addEventListener('click', () => MaxCallBridge.dismissUpdate());
  document.getElementById('btnUpdateRetry')?.addEventListener('click', () => MaxCallBridge.checkUpdate());

  document.getElementById('contactSearch')?.addEventListener('input', (e) =>
    _renderContacts(e.target.value));

  document.getElementById('logSearch')?.addEventListener('input', (e) =>
    _applyLogFilter(e.target.value));

  document.getElementById('feedbackRetry')?.addEventListener('click', () => {
    _fetchFeedbackQueue();
    _fetchFeedbackMy();
  });

  /* feedback section: New/My sub-tabs + outcome dialog */
  document.getElementById('fbSubNew')?.addEventListener('click', () => _switchFeedbackSub('new'));
  document.getElementById('fbSubMy')?.addEventListener('click', () => _switchFeedbackSub('my'));

  const dlg = document.getElementById('fbDialog');
  dlg?.querySelectorAll('[data-score]').forEach((b) =>
    b.addEventListener('click', () => {
      dlg.querySelectorAll('[data-score]').forEach((x) => {
        x.classList.remove('bg-brand', 'text-white');
        x.classList.add('bg-slate-100', 'text-slate-500');
      });
      b.classList.remove('bg-slate-100', 'text-slate-500');
      b.classList.add('bg-brand', 'text-white');
    }));
  document.getElementById('fbDialogCancel')?.addEventListener('click', _closeFeedbackDialog);
  document.getElementById('fbDialogSubmit')?.addEventListener('click', () => {
    if (!_fbDialogTask) return;
    const sel = dlg?.querySelector('[data-score].bg-brand');
    const score = sel ? parseInt(sel.dataset.score, 10) : null;
    const comment = document.getElementById('fbComment')?.value.trim() || '';
    MaxCallBridge.feedbackRespond(_fbDialogTask.id, 'done', { score, comment });
    _closeFeedbackDialog();
    // The native side refetches queue + my list once the server confirms.
  });
}

document.addEventListener('DOMContentLoaded', () => {
  _wireUI();
  _renderContacts('');
  MaxCallBridge.shellReady();
});

// Global entry points called by C++ via ExecuteScript.
window.onRegState = (...a) => MaxCallBridge.onRegState(...a);
window.onCallState = (...a) => MaxCallBridge.onCallState(...a);
window.onIncomingCall = (...a) => MaxCallBridge.onIncomingCall(...a);
window.onMessage = (...a) => MaxCallBridge.onMessage(...a);
window.onContacts = (...a) => MaxCallBridge.onContacts(...a);
window.onAccount = (...a) => MaxCallBridge.onAccount(...a);
window.onPinState = (...a) => MaxCallBridge.onPinState(...a);
  window.onCallHistory = (...a) => MaxCallBridge.onCallHistory(...a);
  window.onUpdateState = (...a) => MaxCallBridge.onUpdateState(...a);

  /* ---------- feedback queue (via C++ bridge) ---------- */
  /** @type {Array<{id:number,phone:string,patient_name:string,visit_date:string,doctor:string,payer:string,visit_amount:number,status:string,attempts:number,max_attempts:number}>} */
  let _feedbackTasks = [];
  /** @type {Array<object>} Claimed tasks waiting for an outcome (My pane). */
  let _myTasks = [];
  /** Task id currently open in the feedback dialog. */
  let _fbDialogTask = null;

  /** Update the notification bubble on the Tasks tab icon + pane counters. */
  function _setFeedbackCounts() {
    const newCount = _feedbackTasks.length;
    const myCount = _myTasks.length;
    const navBadge = document.getElementById('feedbackCountBadge');
    if (navBadge) {
      navBadge.textContent = newCount > 99 ? '99+' : String(newCount);
      navBadge.classList.toggle('hidden', newCount === 0);
    }
    const n = document.getElementById('fbNewCount');
    const m = document.getElementById('fbMyCount');
    if (n) n.textContent = String(newCount);
    if (m) m.textContent = String(myCount);
  }

  /** Ask C++ to fetch the due feedback task queue from the server (New pane). */
  function _fetchFeedbackQueue() {
    const err = document.getElementById('feedbackError');
    if (err) err.classList.add('hidden');
    MaxCallBridge.feedbackQueue();
  }

  /** Ask C++ to fetch the agent's claimed tasks (My pane). */
  function _fetchFeedbackMy() {
    MaxCallBridge.feedbackMy();
  }

  /** Shared task card shell (name + meta line, actions right). */
  function _taskCard() {
    const row = document.createElement('div');
    row.className = 'rounded-2xl border border-slate-100 bg-white p-2.5';
    row.innerHTML =
      '<div class="flex items-center justify-between gap-2">' +
      '<div class="min-w-0"><p class="truncate text-xs font-bold select-text" dir="ltr"></p><p class="truncate text-[11px] text-slate-400 select-text" dir="ltr"></p></div></div>';
    return row;
  }

  /** Compact icon button used in task cards. */
  function _taskBtn(action, title, cls, svg) {
    return '<button data-act="' + action + '" title="' + title + '" aria-label="' + title + '" class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full ' + cls + ' active:scale-95">' + svg + '</button>';
  }

  const CALL_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><path stroke-linecap="round" stroke-linejoin="round" d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/></svg>';
  const INFO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/></svg>';
  const NOANSWER_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><path stroke-linecap="round" d="M10.7 13.3a16 16 0 0 1-2.7-2.7l1.3-1.3a2 2 0 0 0 .5-2.1c-.3-.9-.6-1.9-.7-2.9a2 2 0 0 0-2-1.7H4.1a2 2 0 0 0-2 2.1c.1 1.4.5 2.8 1 4.1m6.6 9.6c1.3.5 2.7.9 4.1 1a2 2 0 0 0 2.1-2v-2.9a2 2 0 0 0-1.7-2c-1-.1-2-.4-2.9-.7a2 2 0 0 0-2.1.5l-.5.5"/><path d="m2 2 20 20"/></svg>';
  const DONE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="h-3.5 w-3.5"><path stroke-linecap="round" stroke-linejoin="round" d="m5 13 4 4L19 7"/></svg>';

  /** Render the due-task list (New pane): the only in-place action is Call. */
  function _renderFeedback(jsonText) {
    let arr = [];
    try { arr = JSON.parse(String(jsonText || '[]')); } catch { arr = []; }
    if (!Array.isArray(arr)) arr = (arr && arr.data) || [];
    _feedbackTasks = arr;
    const list = document.getElementById('feedbackList');
    const empty = document.getElementById('feedbackEmpty');
    const err = document.getElementById('feedbackError');
    if (!list) return;
    list.innerHTML = '';
    if (err) err.classList.add('hidden');
    if (arr.length === 0) {
      if (empty) empty.classList.remove('hidden');
      _setFeedbackCounts();
      return;
    }
    if (empty) empty.classList.add('hidden');
    arr.forEach((t) => {
      const row = _taskCard();
      const date = (t.visit_date || '').slice(0, 10);
      const attempts = String(t.attempts || 0) + '/' + String(t.max_attempts || 3);
      row.innerHTML +=
        '<div class="flex shrink-0 items-center gap-1.5">' +
        _taskBtn('call', 'Call', 'bg-emerald-500 text-white', CALL_SVG) +
        _taskBtn('popup', 'Caller details', 'bg-slate-100 text-slate-500', INFO_SVG) +
        '</div>';
      row.querySelectorAll('p')[0].textContent = t.patient_name || '—';
      row.querySelectorAll('p')[1].textContent = (t.phone || '') + ' · ' + date + ' · ' + attempts;
      row.querySelector('[data-act="call"]').addEventListener('click', () => {
        // ادعاء + اتصال: المهمة تنتقل فوراً إلى My والموظف يتصل بالمريض.
        MaxCallBridge.feedbackClaim(t.id);
        MaxCallBridge.makeCall(t.phone);
        _switchTab('tabDial');
      });
      row.querySelector('[data-act="popup"]').addEventListener('click', () => { MaxCallBridge.openPopup(t.phone); });
      list.appendChild(row);
    });
    _setFeedbackCounts();
    _emit('feedback-queue', { count: arr.length });
  }

  /** Render the agent's claimed tasks (My pane): record outcome here. */
  function _renderMy(jsonText) {
    let arr = [];
    try { arr = JSON.parse(String(jsonText || '[]')); } catch { arr = []; }
    if (!Array.isArray(arr)) arr = (arr && arr.data) || [];
    _myTasks = arr;
    const list = document.getElementById('myList');
    const empty = document.getElementById('myEmpty');
    if (!list) return;
    list.innerHTML = '';
    if (arr.length === 0) {
      if (empty) empty.classList.remove('hidden');
      _setFeedbackCounts();
      return;
    }
    if (empty) empty.classList.add('hidden');
    arr.forEach((t) => {
      const row = _taskCard();
      row.innerHTML +=
        '<div class="flex shrink-0 items-center gap-1.5">' +
        _taskBtn('noanswer', 'No answer', 'bg-amber-50 text-amber-600', NOANSWER_SVG) +
        _taskBtn('done', 'Feedback received', 'bg-emerald-500 text-white', DONE_SVG) +
        '</div>';
      row.querySelectorAll('p')[0].textContent = t.patient_name || '—';
      row.querySelectorAll('p')[1].textContent = (t.phone || '') + ' · feedback pending';
      row.querySelector('[data-act="noanswer"]').addEventListener('click', () => {
        MaxCallBridge.feedbackRespond(t.id, 'no_answer');
      });
      row.querySelector('[data-act="done"]').addEventListener('click', () => {
        _openFeedbackDialog(t);
      });
      list.appendChild(row);
    });
    _setFeedbackCounts();
  }

  /* ---------- feedback recording dialog ---------- */

  /** Open the outcome dialog for a claimed task. */
  function _openFeedbackDialog(task) {
    _fbDialogTask = task;
    const dlg = document.getElementById('fbDialog');
    const who = document.getElementById('fbDialogWho');
    const comment = document.getElementById('fbComment');
    if (!dlg) return;
    if (who) who.textContent = (task.patient_name || '') + (task.phone ? ' · ' + task.phone : '');
    if (comment) comment.value = '';
    dlg.querySelectorAll('[data-score]').forEach((b) => {
      b.classList.remove('bg-brand', 'text-white');
      b.classList.add('bg-slate-100', 'text-slate-500');
    });
    dlg.classList.remove('hidden');
    dlg.classList.add('flex');
  }

  /** Close the outcome dialog without saving. */
  function _closeFeedbackDialog() {
    const dlg = document.getElementById('fbDialog');
    if (!dlg) return;
    dlg.classList.add('hidden');
    dlg.classList.remove('flex');
    _fbDialogTask = null;
  }

  /** Called by C++ via ExecuteScript when the due-task queue arrives from the server. */
  window.onFeedbackQueue = (jsonText) => {
    _renderFeedback(jsonText);
  };

  /** Called by C++ via ExecuteScript when the claimed-task list arrives. */
  window.onFeedbackMy = (jsonText) => {
    _renderMy(jsonText);
  };

  window.MaxCallBridge = MaxCallBridge;
