#include "StdAfx.h"
#include "LoginWebDlg.h"
#include "langpack.h"
#include "settings.h"
#include "global.h"
#include "lib/utf.h"
#include "lib/MSIP.h"
#include "json.h"
#include "define.h"

#include "WebView2.h"
#include <dwmapi.h>

// Forward declare
class LoginWebDlg;

static bool IsAllDigits(const CString& s)
{
	if (s.IsEmpty()) {
		return false;
	}
	for (int i = 0; i < s.GetLength(); i++) {
		if (s[i] < L'0' || s[i] > L'9') {
			return false;
		}
	}
	return true;
}

class LoginMsgHandler : public ICoreWebView2WebMessageReceivedEventHandler
{
public:
	LoginWebDlg* m_dlg;
	ULONG m_refCount;

	LoginMsgHandler(LoginWebDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

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
			PostMessage(m_dlg->m_hWnd, WM_LOGIN_WEB_MESSAGE, 0, (LPARAM)new CString(strMsg));
			CoTaskMemFree(msg);
		}
		return S_OK;
	}
};

// WebView2 Environment callback
class LoginEnvCallback : public ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler
{
public:
	LoginWebDlg* m_dlg;
	ULONG m_refCount;

	LoginEnvCallback(LoginWebDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

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
class LoginCtrlCallback : public ICoreWebView2CreateCoreWebView2ControllerCompletedHandler
{
public:
	LoginWebDlg* m_dlg;
	ULONG m_refCount;

	LoginCtrlCallback(LoginWebDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

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

struct LoginAttempt {
	CString username;
	CString password;
	CString server;
	HWND hwnd;
};

static UINT __cdecl LoginThreadProc(LPVOID pParam)
{
	LoginAttempt* attempt = (LoginAttempt*)pParam;
	CString err;
	bool ok = false;

	CString username = attempt->username;
	CString password = attempt->password;
	CString server = attempt->server;
	HWND hwnd = attempt->hwnd;
	delete attempt;

	if (username.IsEmpty() || password.IsEmpty()) {
		err = _T("Please enter username and password.");
	}
	else {
		if (server.IsEmpty()) {
			server = _T("maxcall.local");
		}
		CString jsonData;
		jsonData.Format(_T("{\"username\":\"%s\",\"password\":\"%s\"}"),
			(LPCTSTR)LoginWebDlg::EscapeLoginJsonPublic(username),
			(LPCTSTR)LoginWebDlg::EscapeLoginJsonPublic(password));

		CString url;
		url.Format(_T("http://%s:3001/api/agent/login"), (LPCTSTR)server);
		CString headers = _T("Content-Type: application/json");

		URLGetAsyncData result = URLGetSync(url, true, jsonData, headers);

		if (result.statusCode == 200) {
			Json::Value response;
			Json::Reader reader;
			bool parsed = reader.parse((LPCSTR)CStringA(result.body), response);

			if (parsed && response.get("ok", false).asBool()) {
				Json::Value extension = response["extension"];
				CString extVal;
				if (extension.isString()) {
					extVal = MSIP::Utf8DecodeUni(extension.asCString());
				}
				else if (extension.isInt() || extension.isUInt()) {
					extVal.Format(_T("%d"), extension.asInt());
				}
				if (extVal.IsEmpty()) {
					err = _T("The account has no assigned extension.");
				}
				else {
					Json::Value displayName = response["name"];
					CString nameVal = displayName.isString() ? MSIP::Utf8DecodeUni(displayName.asCString()) : _T("");

					Json::Value sipServer = response["sip_server"];
					CString sipServerVal = sipServer.isString() && !sipServer.asString().empty()
						? MSIP::Utf8DecodeUni(sipServer.asCString())
						: server;

					accountSettings.AccountDelete(1);

					accountSettings.account.server = sipServerVal;
					accountSettings.account.port = 5060;
					accountSettings.account.username = extVal;
					accountSettings.account.password = password;
					accountSettings.account.authID = extVal;
					accountSettings.account.displayName = nameVal.IsEmpty() ? username : nameVal;
					accountSettings.account.domain = sipServerVal;
					accountSettings.account.rememberPassword = false;
					accountSettings.account.transport = _T("udp");
					accountSettings.accountId = 1;

					ok = true;
				}
			}
			else {
				err = _T("Invalid username or password.");
			}
		}
		else {
			err = _T("Connection failed. Check server address.");
		}
	}

	if (::IsWindow(hwnd)) {
		::PostMessage(hwnd, WM_LOGIN_WEB_RESULT, (WPARAM)ok, (LPARAM)new CString(err));
	}
	return 0;
}

LoginWebDlg::LoginWebDlg(CWnd* pParent)
	: CBaseDialog(LoginWebDlg::IDD, pParent)
{
	loginSuccess = false;
	m_controller = nullptr;
	m_webView = nullptr;
	m_env = nullptr;
	m_webReady = false;
}

LoginWebDlg::~LoginWebDlg(void)
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

void LoginWebDlg::DoDataExchange(CDataExchange* pDX)
{
	CBaseDialog::DoDataExchange(pDX);
}

BEGIN_MESSAGE_MAP(LoginWebDlg, CBaseDialog)
	ON_WM_SIZE()
	ON_WM_CLOSE()
	ON_WM_TIMER()
	ON_MESSAGE(WM_LOGIN_WEB_MESSAGE, &LoginWebDlg::OnLoginWebMessage)
	ON_MESSAGE(WM_LOGIN_WEB_RESULT, &LoginWebDlg::OnLoginWebResult)
END_MESSAGE_MAP()

static CString LoginWindowTitle()
{
	CString title;
	title.Format(_T("%s - Sign in %s"), _T(_GLOBAL_NAME_NICE), _T(_GLOBAL_VERSION));
	return title;
}

BOOL LoginWebDlg::OnInitDialog()
{
	CBaseDialog::OnInitDialog();

	TranslateDialog(this->m_hWnd);

	HICON hIcon = AfxGetApp()->LoadIcon(IDR_MAINFRAME);
	SetIcon(hIcon, TRUE);
	SetIcon(hIcon, FALSE);

	// Beta and stable builds are tested side by side, so the window has to say
	// which one it is even before the page paints.
	SetWindowText(LoginWindowTitle());

	SetClassLongPtr(m_hWnd, GCLP_HBRBACKGROUND, (LONG_PTR)CreateSolidBrush(MAXCALL_SURFACE));

	BOOL darkMode = TRUE;
	DwmSetWindowAttribute(m_hWnd, DWMWA_USE_IMMERSIVE_DARK_MODE, &darkMode, sizeof(darkMode));
	COLORREF titleColor = MAXCALL_TEAL;
	DwmSetWindowAttribute(m_hWnd, DWMWA_CAPTION_COLOR, &titleColor, sizeof(titleColor));
	COLORREF txtColor = MAXCALL_WHITE;
	DwmSetWindowAttribute(m_hWnd, DWMWA_TEXT_COLOR, &txtColor, sizeof(txtColor));

	InitWebView2();

	return TRUE;
}

void LoginWebDlg::InitWebView2()
{
	LoginEnvCallback* cb = new LoginEnvCallback(this);
	CreateCoreWebView2Environment(cb);
}

// The WebView2 bootstrap used to swallow every failure, so a missing runtime or
// a blocked runtime folder produced a completely blank window with nothing in
// the log. Every step now records its HRESULT and reports it to the user.
static void LoginDiag(const CString& step, HRESULT hr)
{
	CString line;
	line.Format(_T("[login] %s hr=0x%08lX"), (LPCTSTR)step, (unsigned long)hr);
	FILE* f = fopen("maxcall_debug.log", "a");
	if (f) {
		fprintf(f, "%ls\n", (LPCTSTR)line);
		fclose(f);
	}
}

static void LoginFail(HWND hWnd, const CString& detail)
{
	LoginDiag(_T("FAILED ") + detail, E_FAIL);
	MessageBox(hWnd,
		_T("MaxCall could not start its display engine.\n\n")
		_T("Install or repair the Microsoft Edge WebView2 Runtime, then run MaxCall again.\n\n")
		_T("Details: ") + detail,
		_T("MaxCall"), MB_ICONERROR | MB_OK);
}

// LoginEnvCallback implementation
HRESULT LoginEnvCallback::Invoke(HRESULT result, ICoreWebView2Environment* env)
{
	LoginDiag(_T("CreateCoreWebView2Environment"), result);
	if (SUCCEEDED(result) && env) {
		m_dlg->m_env = env;
		env->AddRef();

		LoginCtrlCallback* ctrlCb = new LoginCtrlCallback(m_dlg);
		env->CreateCoreWebView2Controller(m_dlg->m_hWnd, ctrlCb);
		ctrlCb->Release();
	}
	else {
		LoginFail(m_dlg->m_hWnd, _T("the WebView2 runtime is unavailable"));
	}
	return S_OK;
}

// LoginCtrlCallback implementation
HRESULT LoginCtrlCallback::Invoke(HRESULT result, ICoreWebView2Controller* controller)
{
	LoginDiag(_T("CreateCoreWebView2Controller"), result);
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
				new LoginMsgHandler(m_dlg),
				&token);

			HRSRC hRes = FindResource(NULL, MAKEINTRESOURCE(IDR_LOGIN_HTML), RT_RCDATA);
			if (!hRes) {
				LoginFail(m_dlg->m_hWnd, _T("the sign-in page is missing from this build"));
			}
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
							CString navigated;
							navigated.Format(_T("NavigateToString login.html chars=%d"), wideLen);
							LoginDiag(navigated, S_OK);
							m_dlg->ArmWebViewWatchdog();
							wv->NavigateToString(htmlStr.GetString());
						}
						else {
							LoginFail(m_dlg->m_hWnd, _T("the sign-in page could not be decoded"));
						}
						UnlockResource(hData);
					}
				}
				FreeResource(hRes);
			}
		}
	}
	else {
		LoginFail(m_dlg->m_hWnd, _T("the WebView2 controller could not be created"));
	}
	return S_OK;
}

// The sign-in page talks to C++ as soon as it loads. If nothing arrives the
// engine never painted, and the honest thing is to say so instead of leaving
// the user staring at a white rectangle.
void LoginWebDlg::ArmWebViewWatchdog()
{
	if (!m_webReady) {
		SetTimer(IDT_LOGIN_WEBVIEW_TIMEOUT, 12000, NULL);
	}
}

void LoginWebDlg::ReportWebViewTimeout()
{
	KillTimer(IDT_LOGIN_WEBVIEW_TIMEOUT);
	if (m_webReady) {
		return;
	}
	LoginDiag(_T("watchdog expired before the page reported ready"), E_FAIL);

	CString message;
	message.Format(
		_T("MaxCall %s could not display its sign-in screen.\n\n")
		_T("The Microsoft Edge WebView2 runtime is either missing or blocked.\n")
		_T("Install or repair it, then start MaxCall again.\n\n")
		_T("Technical details were written to maxcall_debug.log next to this program."),
		(LPCTSTR)_T(_GLOBAL_VERSION));
	::MessageBox(m_hWnd, message, _T("MaxCall"), MB_ICONERROR | MB_OK);
	EndDialog(IDCANCEL);
}

void LoginWebDlg::OnTimer(UINT_PTR nIDEvent)
{
	if (nIDEvent == IDT_LOGIN_WEBVIEW_TIMEOUT) {
		ReportWebViewTimeout();
		return;
	}
}

LRESULT LoginWebDlg::OnLoginWebMessage(WPARAM wParam, LPARAM lParam)
{
	// The first message off the page means it painted and the bridge is alive.
	if (!m_webReady) {
		m_webReady = true;
		KillTimer(IDT_LOGIN_WEBVIEW_TIMEOUT);
	}
	CString* msg = reinterpret_cast<CString*>(lParam);
	if (msg) {
		ProcessLoginMessage(*msg);
		delete msg;
	}
	return 0;
}

void LoginWebDlg::ProcessLoginMessage(CString& message)
{
	Json::Value root;
	Json::Reader reader;
	std::string utf8 = CT2A(message, CP_UTF8);
	if (!reader.parse(utf8, root)) {
		return;
	}
	CString action = LoginJsString(root["action"]);
	if (action != _T("login")) {
		return;
	}
	CString username = LoginJsString(root["username"]);
	CString password = LoginJsString(root["password"]);
	CString server = LoginJsString(root["server"]);
	username.Trim();
	server.Trim();
	if (username.IsEmpty() || password.IsEmpty()) {
		PushLoginError(_T("Please enter username and password."));
		return;
	}
	if (IsAllDigits(username)) {
		PushLoginError(_T("Please sign in with your username, not the extension number."));
		return;
	}
	PushLoginError(_T(""));
	PushLoginBusy(true);

	LoginAttempt* attempt = new LoginAttempt();
	attempt->username = username;
	attempt->password = password;
	attempt->server = server;
	attempt->hwnd = m_hWnd;
	AfxBeginThread(LoginThreadProc, attempt);
}

LRESULT LoginWebDlg::OnLoginWebResult(WPARAM wParam, LPARAM lParam)
{
	bool ok = (wParam != 0);
	CString* err = reinterpret_cast<CString*>(lParam);
	PushLoginBusy(false);
	if (ok) {
		loginSuccess = true;
		EndDialog(IDOK);
	}
	else if (err) {
		PushLoginError(*err);
	}
	if (err) {
		delete err;
	}
	return 0;
}

void LoginWebDlg::ExecuteLoginScript(const CString& js)
{
	if (!m_webView || !IsWindow(m_hWnd)) {
		return;
	}
	ICoreWebView2* wv = static_cast<ICoreWebView2*>(m_webView);
	if (wv) {
		CStringW wJs(js);
		wv->ExecuteScript(wJs.GetString(), nullptr);
	}
}

void LoginWebDlg::PushLoginBusy(bool busy)
{
	CString js;
	js.Format(_T("onLoginBusy(%s)"), busy ? _T("true") : _T("false"));
	ExecuteLoginScript(js);
}

void LoginWebDlg::PushLoginError(const CString& message)
{
	CString js;
	js.Format(_T("onLoginError('%s')"), EscapeLoginJs(message));
	ExecuteLoginScript(js);
}

void LoginWebDlg::OnSize(UINT nType, int cx, int cy)
{
	CBaseDialog::OnSize(nType, cx, cy);
	if (m_controller) {
		ICoreWebView2Controller* ctrl = static_cast<ICoreWebView2Controller*>(m_controller);
		RECT bounds;
		GetClientRect(&bounds);
		ctrl->put_Bounds(bounds);
	}
}

void LoginWebDlg::OnClose()
{
	EndDialog(IDCANCEL);
}

void LoginWebDlg::PostNcDestroy()
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
	CBaseDialog::PostNcDestroy();
}

CString LoginWebDlg::EscapeLoginJs(const CString& input)
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

CString LoginWebDlg::EscapeLoginJsonPublic(const CString& value)
{
	CString escaped;
	for (int i = 0; i < value.GetLength(); i++) {
		switch (value[i]) {
		case L'"': escaped += _T("\\\""); break;
		case L'\\': escaped += _T("\\\\"); break;
		case L'\n': escaped += _T("\\n"); break;
		case L'\r': escaped += _T("\\r"); break;
		case L'\t': escaped += _T("\\t"); break;
		default: escaped += value[i]; break;
		}
	}
	return escaped;
}

CString LoginWebDlg::LoginJsString(const Json::Value& value)
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
