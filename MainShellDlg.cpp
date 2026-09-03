#include "StdAfx.h"
#include "MainShellDlg.h"
#include "langpack.h"
#include "mainDlg.h"
#include "settings.h"
#include "global.h"
#include "lib/utf.h"
#include "lib/MSIP.h"
#include "json.h"

#include "WebView2.h"
#include <dwmapi.h>

// Forward declare
class MainShellDlg;

class ShellMsgHandler : public ICoreWebView2WebMessageReceivedEventHandler
{
public:
	MainShellDlg* m_dlg;
	ULONG m_refCount;

	ShellMsgHandler(MainShellDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

	ULONG STDMETHODCALLTYPE AddRef() override { return ++m_refCount; }
	ULONG STDMETHODCALLTYPE Release() override {
		ULONG count = --m_refCount;
		if (count == 0) delete this;
		return count;
	}
	HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
		if (riid == IID_IUnknown || riid == IID_ICoreWebView2WebMessageReceivedEventHandler) {
			*ppv = static_cast<ICoreWebView2WebMessageReceivedEventHandler*>(this);
			AddRef();
			return S_OK;
		}
		*ppv = nullptr;
		return E_NOINTERFACE;
	}

	HRESULT STDMETHODCALLTYPE Invoke(ICoreWebView2* webview, ICoreWebView2WebMessageReceivedEventArgs* args) override
	{
		LPWSTR msg = nullptr;
		args->TryGetWebMessageAsString(&msg);
		if (msg) {
			CString strMsg(msg);
			PostMessage(m_dlg->m_hWnd, WM_SHELL_MESSAGE, 0, (LPARAM)new CString(strMsg));
			CoTaskMemFree(msg);
		}
		return S_OK;
	}
};

// WebView2 Environment callback
class ShellEnvCallback : public ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler
{
public:
	MainShellDlg* m_dlg;
	ULONG m_refCount;

	ShellEnvCallback(MainShellDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

	ULONG STDMETHODCALLTYPE AddRef() override { return ++m_refCount; }
	ULONG STDMETHODCALLTYPE Release() override {
		ULONG count = --m_refCount;
		if (count == 0) delete this;
		return count;
	}
	HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
		if (riid == IID_IUnknown || riid == IID_ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler) {
			*ppv = static_cast<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler*>(this);
			AddRef();
			return S_OK;
		}
		*ppv = nullptr;
		return E_NOINTERFACE;
	}
	HRESULT STDMETHODCALLTYPE Invoke(HRESULT result, ICoreWebView2Environment* env) override;
};

// WebView2 Controller callback
class ShellCtrlCallback : public ICoreWebView2CreateCoreWebView2ControllerCompletedHandler
{
public:
	MainShellDlg* m_dlg;
	ULONG m_refCount;

	ShellCtrlCallback(MainShellDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

	ULONG STDMETHODCALLTYPE AddRef() override { return ++m_refCount; }
	ULONG STDMETHODCALLTYPE Release() override {
		ULONG count = --m_refCount;
		if (count == 0) delete this;
		return count;
	}
	HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
		if (riid == IID_IUnknown || riid == IID_ICoreWebView2CreateCoreWebView2ControllerCompletedHandler) {
			*ppv = static_cast<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler*>(this);
			AddRef();
			return S_OK;
		}
		*ppv = nullptr;
		return E_NOINTERFACE;
	}
	HRESULT STDMETHODCALLTYPE Invoke(HRESULT result, ICoreWebView2Controller* controller) override;
};

// NavigationCompleted callback — fires after HTML is fully loaded and JS is ready
class ShellNavHandler : public ICoreWebView2NavigationCompletedEventHandler
{
public:
	MainShellDlg* m_dlg;
	ULONG m_refCount;

	ShellNavHandler(MainShellDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

	ULONG STDMETHODCALLTYPE AddRef() override { return ++m_refCount; }
	ULONG STDMETHODCALLTYPE Release() override {
		ULONG count = --m_refCount;
		if (count == 0) delete this;
		return count;
	}
	HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
		if (riid == IID_IUnknown || riid == IID_ICoreWebView2NavigationCompletedEventHandler) {
			*ppv = static_cast<ICoreWebView2NavigationCompletedEventHandler*>(this);
			AddRef();
			return S_OK;
		}
		*ppv = nullptr;
		return E_NOINTERFACE;
	}
	HRESULT STDMETHODCALLTYPE Invoke(ICoreWebView2* sender, ICoreWebView2NavigationCompletedEventArgs* args) override {
		if (m_dlg && ::IsWindow(m_dlg->m_hWnd)) {
			::PostMessage(m_dlg->m_hWnd, WM_SHELL_READY, 0, 0);
		}
		return S_OK;
	}
};

MainShellDlg::MainShellDlg(CWnd* pParent)
	: CBaseDialog(MainShellDlg::IDD, pParent)
{
	m_controller = nullptr;
	m_webView = nullptr;
	m_env = nullptr;
	m_pageReady = false;
	m_minPin = false;
	m_lastCallId = PJSUA_INVALID_ID;
}

MainShellDlg::~MainShellDlg(void)
{
	if (m_controller) {
		ICoreWebView2Controller* ctrl = static_cast<ICoreWebView2Controller*>(m_controller);
		ctrl->Close();
		ctrl->Release();
		m_controller = nullptr;
	}
	if (m_webView) {
		static_cast<ICoreWebView2*>(m_webView)->Release();
		m_webView = nullptr;
	}
	if (m_env) {
		static_cast<ICoreWebView2Environment*>(m_env)->Release();
		m_env = nullptr;
	}
}

void MainShellDlg::DoDataExchange(CDataExchange* pDX)
{
	CBaseDialog::DoDataExchange(pDX);
}

BEGIN_MESSAGE_MAP(MainShellDlg, CBaseDialog)
	ON_WM_SIZE()
	ON_WM_CLOSE()
	ON_WM_SYSCOMMAND()
	ON_WM_TIMER()
	ON_MESSAGE(WM_SHELL_READY, &MainShellDlg::OnShellReady)
	ON_MESSAGE(WM_SHELL_MESSAGE, &MainShellDlg::OnShellMessage)
	ON_MESSAGE(WM_SHELL_HISTORY, &MainShellDlg::OnShellHistory)
END_MESSAGE_MAP()

BOOL MainShellDlg::OnInitDialog()
{
	CBaseDialog::OnInitDialog();

	TranslateDialog(this->m_hWnd);

	HICON hIcon = AfxGetApp()->LoadIcon(IDR_MAINFRAME);
	SetIcon(hIcon, TRUE);
	SetIcon(hIcon, FALSE);

	SetClassLongPtr(m_hWnd, GCLP_HBRBACKGROUND, (LONG_PTR)CreateSolidBrush(MAXCARE_SURFACE));

	BOOL darkMode = TRUE;
	DwmSetWindowAttribute(m_hWnd, DWMWA_USE_IMMERSIVE_DARK_MODE, &darkMode, sizeof(darkMode));
	COLORREF titleColor = MAXCARE_TEAL;
	DwmSetWindowAttribute(m_hWnd, DWMWA_CAPTION_COLOR, &titleColor, sizeof(titleColor));
	COLORREF txtColor = MAXCARE_WHITE;
	DwmSetWindowAttribute(m_hWnd, DWMWA_TEXT_COLOR, &txtColor, sizeof(txtColor));

	InitWebView2();

	return TRUE;
}

void MainShellDlg::InitWebView2()
{
	ShellEnvCallback* cb = new ShellEnvCallback(this);
	CreateCoreWebView2Environment(cb);
}

// ShellEnvCallback implementation
HRESULT ShellEnvCallback::Invoke(HRESULT result, ICoreWebView2Environment* env)
{
	if (SUCCEEDED(result) && env) {
		m_dlg->m_env = env;
		env->AddRef();

		ShellCtrlCallback* ctrlCb = new ShellCtrlCallback(m_dlg);
		env->CreateCoreWebView2Controller(m_dlg->m_hWnd, ctrlCb);
		ctrlCb->Release();
	}
	return S_OK;
}

// ShellCtrlCallback implementation
HRESULT ShellCtrlCallback::Invoke(HRESULT result, ICoreWebView2Controller* controller)
{
	if (SUCCEEDED(result) && controller) {
		m_dlg->m_controller = controller;
		controller->AddRef();

		RECT bounds;
		m_dlg->GetClientRect(&bounds);
		controller->put_Bounds(bounds);

		ICoreWebView2* wv = nullptr;
		controller->get_CoreWebView2(&wv);
		if (wv) {
			m_dlg->m_webView = wv;

			ICoreWebView2Settings* settings = nullptr;
			wv->get_Settings(&settings);
			if (settings) {
				settings->put_IsWebMessageEnabled(TRUE);
				settings->Release();
			}

			EventRegistrationToken token;
			wv->add_WebMessageReceived(
				new ShellMsgHandler(m_dlg),
				&token);

			EventRegistrationToken navToken;
			wv->add_NavigationCompleted(
				new ShellNavHandler(m_dlg),
				&navToken);

			HRSRC hRes = FindResource(NULL, MAKEINTRESOURCE(IDR_MAIN_SHELL_HTML), RT_RCDATA);
			if (hRes) {
				HGLOBAL hData = LoadResource(NULL, hRes);
				if (hData) {
					DWORD size = SizeofResource(NULL, hRes);
					char* data = (char*)LockResource(hData);
					if (data) {
						int wideLen = MultiByteToWideChar(CP_UTF8, 0, data, size, NULL, 0);
						if (wideLen > 0) {
							CStringW htmlStr;
							MultiByteToWideChar(CP_UTF8, 0, data, size, htmlStr.GetBuffer(wideLen), wideLen);
							htmlStr.ReleaseBuffer(wideLen);
							wv->NavigateToString(htmlStr.GetString());
						}
						UnlockResource(hData);
					}
				}
				FreeResource(hRes);
			}
			// لا نرسل WM_SHELL_READY هنا — ShellNavHandler هو من يرسله بعد اكتمال التحميل
		}
	}
	return S_OK;
}

LRESULT MainShellDlg::OnShellReady(WPARAM wParam, LPARAM lParam)
{
	FILE* dbg = fopen("maxcall_debug.log", "a");
	if (dbg) { fprintf(dbg, "shell: webview-ready\n"); fclose(dbg); }
	m_pageReady = true;
	PushSnapshot();
	return 0;
}

LRESULT MainShellDlg::OnShellMessage(WPARAM wParam, LPARAM lParam)
{
	CString* msg = reinterpret_cast<CString*>(lParam);
	if (msg) {
		ProcessShellMessage(*msg);
		delete msg;
	}
	return 0;
}

pjsua_call_id MainShellDlg::ResolveCallId(int requested)
{
	if (pjsua_var.state != PJSUA_STATE_RUNNING) {
		return PJSUA_INVALID_ID;
	}
	if (requested >= 0 && requested < (int)PJSUA_MAX_CALLS && pjsua_call_is_active(requested)) {
		return requested;
	}
	if (mainDlg && IsWindow(mainDlg->m_hWnd)) {
		pjsua_call_id current = mainDlg->CurrentCallId();
		if (current >= 0 && current < (int)PJSUA_MAX_CALLS && pjsua_call_is_active(current)) {
			return current;
		}
	}
	return PJSUA_INVALID_ID;
}

void MainShellDlg::ProcessShellMessage(CString& message)
{
	if (!mainDlg || !IsWindow(mainDlg->m_hWnd)) {
		return;
	}
	Json::Value root;
	Json::Reader reader;
	std::string utf8 = CT2A(message, CP_UTF8);
	if (!reader.parse(utf8, root)) {
		return;
	}
	CString action = JsStringToCString(root["action"]);

	FILE* dbg = fopen("maxcall_debug.log", "a");
	if (dbg) { fprintf(dbg, "shell: %ls\n", (LPCTSTR)action); fclose(dbg); }

	if (action == _T("shellReady") || action == _T("getData")) {
		m_pageReady = true;
		PushSnapshot();
		PushContacts();
		PushAccount();
		FetchHistory();
		return;
	}
	if (action == _T("openSettings")) {
		mainDlg->OnMenuSettings();
		return;
	}
	if (action == _T("quitApp")) {
		mainDlg->OnMenuExit();
		return;
	}
	if (action == _T("minimizeApp")) {
		// التصغير يثبّت النافذة تلقائياً حتى لا تضيع بين النوافذ،
		// والتكبير يعيد حالة التثبيت السابقة (انظر OnSize).
		if (!accountSettings.alwaysOnTop) {
			m_minPin = true;
			accountSettings.alwaysOnTop = 1;
			SetWindowPos(&wndTopMost, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
			PushPinState(true);
		}
		ShowWindow(SW_MINIMIZE);
		return;
	}
	if (action == _T("openPopup")) {
		CString number = JsStringToCString(root["number"]);
		number.Trim();
		if (!number.IsEmpty()) {
			CString name = mainDlg->pageContacts
				? mainDlg->pageContacts->GetNameByNumber(number) : _T("");
			mainDlg->ShowCrmPopup(number, name, PJSUA_INVALID_ID);
		}
		return;
	}
	if (action == _T("pinToggle")) {
		bool on = root.isMember("on") ? root["on"].asBool() : !accountSettings.alwaysOnTop;
		accountSettings.alwaysOnTop = on ? 1 : 0;
		mainDlg->AccountSettingsPendingSave();
		SetWindowPos(on ? &wndTopMost : &wndNoTopMost, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
		PushPinState(on);
		return;
	}
	if (action == _T("makeCall")) {
		CString number = JsStringToCString(root["number"]);
		number.Trim();
		if (!number.IsEmpty()) {
			mainDlg->MakeCall(number);
		}
		return;
	}
	if (action == _T("hangup")) {
		pjsua_call_id id = ResolveCallId(JsInt(root["callId"], -1));
		if (id != PJSUA_INVALID_ID) {
			msip_call_hangup_fast(id);
		}
		return;
	}
	if (action == _T("answer")) {
		// الرد المباشر على المكالمة (CommandCallAnswer يعتمد على نافذة
		// الرنين الأصلية وهي معطلة في الوضع الحديث)
		pjsua_call_id id = ResolveCallId(JsInt(root["callId"], -1));
		if (id != PJSUA_INVALID_ID) {
			pjsua_call_answer(id, 200, NULL, NULL);
		}
		else {
			mainDlg->CommandCallAnswer();
		}
		return;
	}
	if (action == _T("hold")) {
		pjsua_call_id id = ResolveCallId(JsInt(root["callId"], -1));
		if (id != PJSUA_INVALID_ID && root.isMember("on")) {
			pjsua_call_info info;
			if (pjsua_call_get_info(id, &info) == PJ_SUCCESS) {
				if (root["on"].asBool()) {
					msip_call_hold(&info);
				}
				else {
					msip_call_unhold(&info);
				}
			}
		}
		return;
	}
	if (action == _T("transfer")) {
		pjsua_call_id id = ResolveCallId(JsInt(root["callId"], -1));
		CString target = JsStringToCString(root["target"]);
		target.Trim();
		if (id != PJSUA_INVALID_ID && !target.IsEmpty()) {
			CString commands;
			CString formatted = FormatNumber(target, &commands);
			pj_str_t pj_uri = MSIP::StrToPjStr(formatted);
			pjsua_call_xfer(id, &pj_uri, NULL);
		}
		return;
	}
	if (action == _T("sendDTMF")) {
		pjsua_call_id id = ResolveCallId(JsInt(root["callId"], -1));
		CString digit = JsStringToCString(root["digit"]);
		digit.Trim();
		if (id != PJSUA_INVALID_ID && !digit.IsEmpty()) {
			msip_call_dial_dtmf(id, digit.Left(1));
		}
		return;
	}
	if (action == _T("setPresence")) {
		CString status = JsStringToCString(root["status"]);
		if (status == _T("offline")) {
			mainDlg->PublishStatus(false);
		}
		else if (status == _T("busy")) {
			mainDlg->SwitchDND(1);
			mainDlg->PublishStatus(true);
		}
		else {
			// available / away — لا توجد واجهة Away مميزة، ننشر متاحاً
			mainDlg->SwitchDND(0);
			mainDlg->PublishStatus(true);
		}
		return;
	}
}

void MainShellDlg::ExecuteShellScript(const CString& js)
{
	if (!m_webView || !m_pageReady || !IsWindow(m_hWnd)) {
		return;
	}
	FILE* dbg = fopen("maxcall_debug.log", "a");
	if (dbg) {
		CString preview = js.Left(80);
		fprintf(dbg, "shell-tx: %ls\n", (LPCTSTR)preview);
		fclose(dbg);
	}
	ICoreWebView2* wv = static_cast<ICoreWebView2*>(m_webView);
	if (wv) {
		CStringW wJs(js);
		wv->ExecuteScript(wJs.GetString(), nullptr);
	}
}

void MainShellDlg::PushRegState(bool registered, const CString& message)
{
	CString js;
	js.Format(_T("onRegState(%s, '%s')"),
		registered ? _T("true") : _T("false"), EscapeJs(message));
	ExecuteShellScript(js);
}

void MainShellDlg::PushIncomingCall(const CString& number, const CString& name, pjsua_call_id call_id)
{
	CString js;
	js.Format(_T("onIncomingCall('%s', '%s', %d)"),
		EscapeJs(number), EscapeJs(name), call_id);
	ExecuteShellScript(js);
}

void MainShellDlg::PushCallState(pjsua_call_id call_id, const CString& state, const CString& number, const CString& name)
{
	// تخزين مؤقت: أحداث التعليق تصل بلا رقم/اسم فنعيد استخدام آخر قيم معروفة
	if (!number.IsEmpty()) {
		m_lastCallId = call_id;
		m_lastNumber = number;
		m_lastName = name;
	}
	CString showNumber = number;
	CString showName = name;
	if (showNumber.IsEmpty() && call_id == m_lastCallId) {
		showNumber = m_lastNumber;
		showName = m_lastName;
	}
	if (state == _T("ended") && call_id == m_lastCallId) {
		m_lastCallId = PJSUA_INVALID_ID;
		m_lastNumber.Empty();
		m_lastName.Empty();
		// تحديث سجل السيرفر بعد الإنهاء مباشرة
		SetTimer(2, 4000, NULL);
	}
	CString js;
	js.Format(_T("onCallState(%d, '%s', '%s', '%s')"),
		call_id, state, EscapeJs(showNumber), EscapeJs(showName));
	ExecuteShellScript(js);
}

void MainShellDlg::PushMessage(const CString& from, const CString& text)
{
	CString js;
	js.Format(_T("onMessage('%s', '%s')"), EscapeJs(from), EscapeJs(text));
	ExecuteShellScript(js);
}

void MainShellDlg::PushContacts()
{
	if (!mainDlg || !IsWindow(mainDlg->m_hWnd) || !mainDlg->pageContacts) {
		return;
	}
	Json::Value arr(Json::arrayValue);
	POSITION pos = mainDlg->pageContacts->contacts.GetHeadPosition();
	while (pos) {
		Contact* c = mainDlg->pageContacts->contacts.GetNext(pos);
		if (!c) {
			continue;
		}
		CString number = c->number;
		if (number.IsEmpty()) {
			number = c->phone;
		}
		if (number.IsEmpty()) {
			number = c->mobile;
		}
		if (number.IsEmpty()) {
			continue;
		}
		CString name = c->name;
		if (name.IsEmpty()) {
			name = number;
		}
		Json::Value item;
		std::string u8num = CT2A(number, CP_UTF8);
		std::string u8name = CT2A(name, CP_UTF8);
		item["number"] = u8num;
		item["name"] = u8name;
		item["presence"] = c->presence ? true : false;
		arr.append(item);
	}
	Json::FastWriter writer;
	std::string json = writer.write(arr);
	// JSON مبني بـ UTF-8 — نحوّله لـ Unicode قبل الحقن في السكربت
	wchar_t* ucs2 = NULL;
	Utf8DecodeCP((char*)json.c_str(), CP_UTF8, &ucs2);
	CString js;
	if (ucs2) {
		js.Format(_T("onContacts('%s')"), EscapeJs(CString(ucs2)));
		free(ucs2);
		ExecuteShellScript(js);
	}
}

void MainShellDlg::PushAccount()
{
	if (pjsua_var.state != PJSUA_STATE_RUNNING || !pjsua_acc_is_valid(account)) {
		return;
	}
	pjsua_acc_info info;
	if (pjsua_acc_get_info(account, &info) != PJ_SUCCESS) {
		return;
	}
	CString uri = MSIP::PjToStr(&info.acc_uri, TRUE);
	SIPURI sipuri;
	MSIP::ParseSIPURI(uri, &sipuri);
	CString user = sipuri.user;
	CString domain = MSIP::RemovePort(sipuri.domain);
	if (user.IsEmpty()) {
		return;
	}
	CString display = accountSettings.account.displayName;
	CString js;
	js.Format(_T("onAccount('%s', '%s', '%s')"),
		EscapeJs(user), EscapeJs(domain), EscapeJs(display));
	ExecuteShellScript(js);
}

void MainShellDlg::PushPinState(bool on)
{
	CString js;
	js.Format(_T("onPinState(%s)"), on ? _T("true") : _T("false"));
	ExecuteShellScript(js);
}

void MainShellDlg::FetchHistory()
{
	if (!IsWindow(m_hWnd)) {
		return;
	}
	CString agent = accountSettings.account.username;
	agent.Trim();
	if (agent.IsEmpty()) {
		return;
	}
	CString url;
	url.Format(_T("http://192.168.1.165:3001/api/calls/history?agent=%s&limit=50"), (LPCTSTR)agent);
	URLGetAsync(url, m_hWnd, WM_SHELL_HISTORY);
}

LRESULT MainShellDlg::OnShellHistory(WPARAM wParam, LPARAM lParam)
{
	URLGetAsyncData* result = (URLGetAsyncData*)wParam;
	if (result) {
		if (result->statusCode >= 200 && result->statusCode < 300) {
			CStringA bodyA = result->body;
			if (!bodyA.IsEmpty()) {
				wchar_t* ucs2 = NULL;
				Utf8DecodeCP((char*)(LPCSTR)bodyA, CP_UTF8, &ucs2);
				if (ucs2) {
					CString js;
					js.Format(_T("onCallHistory('%s')"), EscapeJs(CString(ucs2)));
					free(ucs2);
					ExecuteShellScript(js);
				}
			}
		}
		delete result;
	}
	return 0;
}

void MainShellDlg::OnTimer(UINT_PTR nIDEvent)
{
	if (nIDEvent == 2) {
		KillTimer(2);
		// تحديث السجل بعد انتهاء المكالمة (يُكتب CDR عند الإنهاء)
		FetchHistory();
		return;
	}
	CDialog::OnTimer(nIDEvent);
}

void MainShellDlg::PushSnapshot()
{
	if (!m_pageReady || !mainDlg || !IsWindow(mainDlg->m_hWnd)) {
		return;
	}
	if (pjsua_var.state == PJSUA_STATE_RUNNING) {
		// حالة التسجيل الحالية
		pjsua_acc_id accs[PJSUA_MAX_ACC];
		unsigned count = PJSUA_MAX_ACC;
		bool registered = false;
		if (pjsua_enum_accs(accs, &count) == PJ_SUCCESS) {
			for (unsigned i = 0; i < count; i++) {
				pjsua_acc_info info;
				if (pjsua_acc_get_info(accs[i], &info) == PJ_SUCCESS && info.status == 200) {
					registered = true;
					break;
				}
			}
		}
		PushRegState(registered, registered ? _T("Registered") : _T("Not registered"));
		PushAccount();
		PushContacts();
		PushPinState(accountSettings.alwaysOnTop != 0);
		// المكالمة النشطة الحالية إن وجدت (مع حالة التعليق الفعلية)
		pjsua_call_id current = mainDlg->CurrentCallId();
		if (current != PJSUA_INVALID_ID && pjsua_call_is_active(current)) {
			pjsua_call_info info;
			if (pjsua_call_get_info(current, &info) == PJ_SUCCESS) {
				CString num = MSIP::PjToStr(&info.remote_info, TRUE);
				SIPURI sipuri;
				MSIP::ParseSIPURI(num, &sipuri);
				CString shellNum = !sipuri.user.IsEmpty() ? sipuri.user : sipuri.domain;
				CString shellName = mainDlg->pageContacts
					? mainDlg->pageContacts->GetNameByNumber(shellNum) : _T("");
				if (info.state == PJSIP_INV_STATE_CONFIRMED) {
					if (info.media_status == PJSUA_CALL_MEDIA_LOCAL_HOLD
						|| info.media_status == PJSUA_CALL_MEDIA_NONE) {
						PushCallState(current, _T("held"), shellNum, shellName);
					}
					else {
						PushCallState(current, _T("active"), shellNum, shellName);
					}
				}
				else {
					PushCallState(current, _T("ringing"), shellNum, shellName);
				}
			}
		}
	}
}

void MainShellDlg::Restore()
{
	PushSnapshot();
	ShowWindow(SW_SHOWNORMAL);
	SetForegroundWindow();
}

void MainShellDlg::OnClose()
{
	ShowWindow(SW_HIDE);
	// أمان: لا نترك المستخدم بلا أي نافذة — أعد الكلاسيكية إن كانت مخفية
	if (mainDlg && IsWindow(mainDlg->m_hWnd) && !mainDlg->IsWindowVisible()) {
		mainDlg->ShowWindow(SW_SHOW);
	}
}

void MainShellDlg::OnSysCommand(UINT nID, LPARAM lParam)
{
	if ((nID & 0xFFF0) == SC_CLOSE) {
		ShowWindow(SW_HIDE);
		if (mainDlg && IsWindow(mainDlg->m_hWnd) && !mainDlg->IsWindowVisible()) {
			mainDlg->ShowWindow(SW_SHOW);
		}
		return;
	}
	CBaseDialog::OnSysCommand(nID, lParam);
}

void MainShellDlg::PostNcDestroy()
{
	// Do NOT call delete this - mainDlg manages lifetime via DestroyWindow
	// Just cleanup WebView2
	if (m_controller) {
		ICoreWebView2Controller* ctrl = static_cast<ICoreWebView2Controller*>(m_controller);
		ctrl->Close();
		ctrl->Release();
		m_controller = nullptr;
	}
	if (m_webView) {
		static_cast<ICoreWebView2*>(m_webView)->Release();
		m_webView = nullptr;
	}
	if (m_env) {
		static_cast<ICoreWebView2Environment*>(m_env)->Release();
		m_env = nullptr;
	}
	CBaseDialog::PostNcDestroy();
}

void MainShellDlg::OnSize(UINT nType, int cx, int cy)
{
	CBaseDialog::OnSize(nType, cx, cy);
	if (m_controller) {
		ICoreWebView2Controller* ctrl = static_cast<ICoreWebView2Controller*>(m_controller);
		RECT bounds;
		GetClientRect(&bounds);
		ctrl->put_Bounds(bounds);
	}
	// إلغاء التثبيت التلقائي بعد التكبير من التصغير
	if (nType == SIZE_RESTORED && m_minPin) {
		m_minPin = false;
		accountSettings.alwaysOnTop = 0;
		mainDlg->AccountSettingsPendingSave();
		SetWindowPos(&wndNoTopMost, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
		PushPinState(false);
	}
}

CString MainShellDlg::EscapeJs(const CString& input)
{
	CString escaped;
	for (int i = 0; i < input.GetLength(); i++) {
		wchar_t c = input[i];
		if (c == L'\'') escaped += _T("\\'");
		else if (c == L'"') escaped += _T("\\\"");
		else if (c == L'\\') escaped += _T("\\\\");
		else if (c == L'\n') escaped += _T("\\n");
		else if (c == L'\r') escaped += _T("\\r");
		else if (c == L'\t') escaped += _T("\\t");
		else if (c < 0x20) escaped.AppendFormat(_T("\\u%04x"), c);
		else escaped += c;
	}
	return escaped;
}

CString MainShellDlg::JsStringToCString(const Json::Value& value)
{
	if (!value.isString()) {
		return _T("");
	}
	std::string utf8 = value.asString();
	wchar_t* ucs2 = NULL;
	Utf8DecodeCP((char*)utf8.c_str(), CP_UTF8, &ucs2);
	CString res;
	if (ucs2) {
		res = ucs2;
		free(ucs2);
	}
	return res;
}

int MainShellDlg::JsInt(const Json::Value& value, int fallback)
{
	if (value.isInt()) {
		return value.asInt();
	}
	if (value.isUInt()) {
		return (int)value.asUInt();
	}
	return fallback;
}
