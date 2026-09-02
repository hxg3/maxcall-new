#include "StdAfx.h"
#include "CrmPopupDlg.h"
#include "langpack.h"
#include "mainDlg.h"
#include "settings.h"
#include "global.h"
#include "lib/utf.h"
#include "json.h"

#include "WebView2.h"
#include <dwmapi.h>

// Forward declare
class CrmPopupDlg;

class WebMessageReceivedHandler : public ICoreWebView2WebMessageReceivedEventHandler
{
public:
	CrmPopupDlg* m_dlg;
	ULONG m_refCount;

	WebMessageReceivedHandler(CrmPopupDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

	ULONG STDMETHODCALLTYPE AddRef() override { return ++m_refCount; }
	ULONG STDMETHODCALLTYPE Release() override {
		ULONG count = --m_refCount;
		if (count == 0) delete this;
		return count;
	}
	HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
		if (riid == IID_ICoreWebView2WebMessageReceivedEventHandler) {
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
			PostMessage(m_dlg->m_hWnd, WM_WEBVIEW_MESSAGE, 0, (LPARAM)new CString(strMsg));
			CoTaskMemFree(msg);
		}
		return S_OK;
	}
};

// WebView2 Environment callback
class EnvCallback : public ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler
{
public:
	CrmPopupDlg* m_dlg;
	ULONG m_refCount;

	EnvCallback(CrmPopupDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

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
class CtrlCallback : public ICoreWebView2CreateCoreWebView2ControllerCompletedHandler
{
public:
	CrmPopupDlg* m_dlg;
	ULONG m_refCount;

	CtrlCallback(CrmPopupDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

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
class NavCompletedHandler : public ICoreWebView2NavigationCompletedEventHandler
{
public:
	CrmPopupDlg* m_dlg;
	ULONG m_refCount;

	NavCompletedHandler(CrmPopupDlg* dlg) : m_dlg(dlg), m_refCount(1) {}

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
			::PostMessage(m_dlg->m_hWnd, WM_WEBVIEW_READY, 0, 0);
		}
		return S_OK;
	}
};

CrmPopupDlg::CrmPopupDlg(CWnd* pParent)
	: CBaseDialog(CrmPopupDlg::IDD, pParent)
{
	call_id = PJSUA_INVALID_ID;
	m_controller = nullptr;
	m_webView = nullptr;
	m_env = nullptr;
}

CrmPopupDlg::~CrmPopupDlg(void)
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

void CrmPopupDlg::DoDataExchange(CDataExchange* pDX)
{
	CBaseDialog::DoDataExchange(pDX);
}

BEGIN_MESSAGE_MAP(CrmPopupDlg, CBaseDialog)
	ON_WM_SIZE()
	ON_WM_CLOSE()
	ON_WM_SYSCOMMAND()
	ON_MESSAGE(WM_WEBVIEW_READY, &CrmPopupDlg::OnWebViewReady)
	ON_MESSAGE(WM_WEBVIEW_MESSAGE, &CrmPopupDlg::OnWebViewMessage)
	ON_MESSAGE(WM_CRM_LOAD_RESULT, &CrmPopupDlg::OnCrmLoadResult)
	ON_MESSAGE(WM_CRM_SAVE_RESULT, &CrmPopupDlg::OnCrmSaveResult)
END_MESSAGE_MAP()

BOOL CrmPopupDlg::OnInitDialog()
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

	CRect screenRect;
	SystemParametersInfo(SPI_GETWORKAREA, 0, &screenRect, 0);
	CRect dlgRect;
	GetWindowRect(&dlgRect);
	int x = screenRect.right - dlgRect.Width() - 20;
	int y = screenRect.top + 20;
	SetWindowPos(&wndTopMost, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE);

	InitWebView2();

	return TRUE;
}

void CrmPopupDlg::InitWebView2()
{
	EnvCallback* cb = new EnvCallback(this);
	CreateCoreWebView2Environment(cb);
}

// EnvCallback implementation
HRESULT EnvCallback::Invoke(HRESULT result, ICoreWebView2Environment* env)
{
	if (SUCCEEDED(result) && env) {
		m_dlg->m_env = env;
		env->AddRef();

		CtrlCallback* ctrlCb = new CtrlCallback(m_dlg);
		env->CreateCoreWebView2Controller(m_dlg->m_hWnd, ctrlCb);
		ctrlCb->Release();
	}
	return S_OK;
}

// CtrlCallback implementation
HRESULT CtrlCallback::Invoke(HRESULT result, ICoreWebView2Controller* controller)
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
				new WebMessageReceivedHandler(m_dlg),
				&token);

			// نسجّل حدث NavigationCompleted لنعرف متى انتهى تحميل الـ HTML فعلياً
			EventRegistrationToken navToken;
			wv->add_NavigationCompleted(
				new NavCompletedHandler(m_dlg),
				&navToken);

			HRSRC hRes = FindResource(NULL, MAKEINTRESOURCE(IDR_CRM_POPUP_HTML), RT_RCDATA);
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
			// لا نرسل WM_WEBVIEW_READY هنا — NavCompletedHandler هو من يرسله بعد اكتمال التحميل
		}
	}
	return S_OK;
}

LRESULT CrmPopupDlg::OnWebViewReady(WPARAM wParam, LPARAM lParam)
{
	UpdateWebView();
	LoadCallerInfo();
	return 0;
}

LRESULT CrmPopupDlg::OnWebViewMessage(WPARAM wParam, LPARAM lParam)
{
	CString* msg = reinterpret_cast<CString*>(lParam);
	if (msg) {
		ProcessWebViewMessage(*msg);
		delete msg;
	}
	return 0;
}

void CrmPopupDlg::ProcessWebViewMessage(CString& message)
{
	FILE* fp = fopen("maxcall_debug.log", "a");
	if(fp) { fprintf(fp, "ProcessWebViewMessage called\n"); fclose(fp); }

	Json::Value root;
	Json::Reader reader;
	std::string utf8 = CT2A(message, CP_UTF8);
	if (reader.parse(utf8, root)) {
		CString action = JsonStringToCString(root["action"]);
		if (action == _T("save")) {
			if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "Action is save\n"); fclose(fp); }
			callerName = JsonStringToCString(root["name"]);
			notes = JsonStringToCString(root["notes"]);

			if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "Calling SaveCallerInfo\n"); fclose(fp); }
			SaveCallerInfo();
			// لا نُخفي النافذة هنا — ننتظر نتيجة الـ HTTP POST في OnCrmSaveResult
			if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "SaveCallerInfo returned, waiting for server response\n"); fclose(fp); }
			return;
		}
		if (action == _T("dismiss")) {
			if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "Action is dismiss\n"); fclose(fp); }
			ShowWindow(SW_HIDE);
			return;
		}
	}
}

void CrmPopupDlg::LoadCallerInfo()
{
	// استخراج الرقم النظيف من SIP URI إن وُجدت
	CString cleanNumber = callerNumber;
	int atPos = cleanNumber.Find(_T('@'));
	if (atPos != -1) {
		cleanNumber = cleanNumber.Left(atPos);
	}
	cleanNumber.TrimLeft(_T("sipSIP:"));
	cleanNumber.Trim();
	if (cleanNumber.IsEmpty()) cleanNumber = callerNumber;

	CString url;
	url.Format(_T("http://192.168.1.165:3001/api/callers/%s"), UrlEncodeCallerNumber(cleanNumber));

	URLGetAsync(url, m_hWnd, WM_CRM_LOAD_RESULT);
}

void CrmPopupDlg::UpdateWebView()
{
	if (!m_webView) return;

	// استخراج الرقم النظيف من SIP URI إن وُجدت
	CString cleanNumber = callerNumber;
	int atPos = cleanNumber.Find(_T('@'));
	if (atPos != -1) {
		cleanNumber = cleanNumber.Left(atPos);
	}
	cleanNumber.TrimLeft(_T("sipSIP:"));
	cleanNumber.Trim();
	if (cleanNumber.IsEmpty()) cleanNumber = callerNumber;

	CString escapedNumber = EscapeJson(cleanNumber);
	CString escapedName = EscapeJson(callerName);
	CString escapedNotes = EscapeJson(notes);

	CString js;
	js.Format(_T("updateCallerInfo('%s', '%s', '%s', %d)"),
		escapedNumber, escapedName, escapedNotes, call_id);

	ICoreWebView2* wv = static_cast<ICoreWebView2*>(m_webView);
	if (wv) {
		CStringW wJs(js);
		wv->ExecuteScript(wJs.GetString(), nullptr);
	}
}

void CrmPopupDlg::SaveCallerInfo()
{
	FILE* fp = fopen("maxcall_debug.log", "a");
	if(fp) { fprintf(fp, "SaveCallerInfo start\n"); fclose(fp); }

	// استخراج الرقم النظيف من SIP URI إن وُجدت
	CString cleanNumber = callerNumber;
	int atPos = cleanNumber.Find(_T('@'));
	if (atPos != -1) {
		cleanNumber = cleanNumber.Left(atPos);
	}
	cleanNumber.TrimLeft(_T("sipSIP:"));
	cleanNumber.Trim();
	if (cleanNumber.IsEmpty()) cleanNumber = callerNumber;

	CString url = _T("http://192.168.1.165:3001/api/callers");

	if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "Calling EscapeJson\n"); fclose(fp); }
	CString phone = EscapeJson(cleanNumber);
	CString name = EscapeJson(callerName);
	CString callerNotes = EscapeJson(notes);

	if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "Formatting postData\n"); fclose(fp); }
	CString postData;
	postData.Format(_T("{\"phone\":\"%s\",\"name\":\"%s\",\"notes\":\"%s\"}"), (LPCTSTR)phone, (LPCTSTR)name, (LPCTSTR)callerNotes);

	CString headers = _T("Content-Type: application/json; charset=utf-8");

	if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "Calling URLGetAsync\n"); fclose(fp); }
	URLGetAsync(url, m_hWnd, WM_CRM_SAVE_RESULT, true, postData, headers);
	if(fp = fopen("maxcall_debug.log", "a")) { fprintf(fp, "URLGetAsync returned\n"); fclose(fp); }
}

LRESULT CrmPopupDlg::OnCrmLoadResult(WPARAM wParam, LPARAM lParam)
{
	URLGetAsyncData* result = (URLGetAsyncData*)wParam;
	if (result) {
		if (result->statusCode >= 200 && result->statusCode < 300 && !result->body.IsEmpty()) {
			Json::Value caller;
			Json::Reader reader;
			if (reader.parse((LPCSTR)result->body, caller)) {
				CString savedName = JsonStringToCString(caller["name"]);
				if (!savedName.IsEmpty()) {
					callerName = savedName;
				}
				if (caller["notes"].isString()) {
					notes = JsonStringToCString(caller["notes"]);
				}
			}
		}
		UpdateWebView();
		delete result;
	}
	return 0;
}

LRESULT CrmPopupDlg::OnCrmSaveResult(WPARAM wParam, LPARAM lParam)
{
	URLGetAsyncData* result = (URLGetAsyncData*)wParam;
	FILE* fp = fopen("maxcall_debug.log", "a");
	if(fp) { fprintf(fp, "OnCrmSaveResult called, statusCode=%lu\n", result ? result->statusCode : 0); fclose(fp); }
	if (result) {
		bool ok = (result->statusCode >= 200 && result->statusCode < 300);
		// أخبر الـ JavaScript بنتيجة الحفظ
		if (m_webView && ::IsWindow(m_hWnd)) {
			CString js;
			js.Format(_T("notifySaveResult(%s)"), ok ? _T("true") : _T("false"));
			ICoreWebView2* wv = static_cast<ICoreWebView2*>(m_webView);
			if (wv) {
				CStringW wJs(js);
				wv->ExecuteScript(wJs.GetString(), nullptr);
			}
		}
		delete result;
	}
	return 0;
}

void CrmPopupDlg::OnClose()
{
	ShowWindow(SW_HIDE);
}

void CrmPopupDlg::OnSysCommand(UINT nID, LPARAM lParam)
{
	if ((nID & 0xFFF0) == SC_CLOSE) {
		ShowWindow(SW_HIDE);
		return;
	}
	CBaseDialog::OnSysCommand(nID, lParam);
}

void CrmPopupDlg::PostNcDestroy()
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

void CrmPopupDlg::OnSize(UINT nType, int cx, int cy)
{
	CBaseDialog::OnSize(nType, cx, cy);
	if (m_controller) {
		ICoreWebView2Controller* ctrl = static_cast<ICoreWebView2Controller*>(m_controller);
		RECT bounds;
		GetClientRect(&bounds);
		ctrl->put_Bounds(bounds);
	}
}

void CrmPopupDlg::Restore()
{
	UpdateWebView();
	ShowWindow(SW_SHOWNORMAL);
	SetForegroundWindow();
	SetTimer(1, 120000, NULL);
}

CString CrmPopupDlg::JsonStringToCString(const Json::Value& value)
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

CString CrmPopupDlg::EscapeJson(const CString& input)
{
	CString escaped;
	for (int i = 0; i < input.GetLength(); i++) {
		wchar_t c = input[i];
		if (c == L'"') escaped += _T("\\\"");
		else if (c == L'\\') escaped += _T("\\\\");
		else if (c == L'\b') escaped += _T("\\b");
		else if (c == L'\f') escaped += _T("\\f");
		else if (c == L'\n') escaped += _T("\\n");
		else if (c == L'\r') escaped += _T("\\r");
		else if (c == L'\t') escaped += _T("\\t");
		else if (c < 0x20) escaped.AppendFormat(_T("\\u%04x"), c);
		else escaped += c;
	}
	return escaped;
}

CString CrmPopupDlg::UrlEncodeCallerNumber(const CString& number)
{
	char* utf8 = Utf8EncodeUcs2(number);
	CStringA encoded = urlencode(utf8 ? CStringA(utf8) : CStringA());
	if (utf8) {
		free(utf8);
	}
	return CString(encoded);
}
