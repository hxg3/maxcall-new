#pragma once

#include "resource.h"
#include "BaseDialog.h"
#include <pjsua-lib/pjsua.h>
#include "json.h"

struct ICoreWebView2Controller;
struct ICoreWebView2;
struct ICoreWebView2Environment;

#define WM_SHELL_READY (WM_USER + 110)
#define WM_SHELL_MESSAGE (WM_USER + 111)

// MainShellDlg — النافذة الرئيسية الحديثة (WebView2 Shell).
// تستضيف web-shell/ المضمّنة (IDR_MAIN_SHELL_HTML) بملء العميل،
// وتترجم رسائل JS إلى أوامر PJSUA عبر CmainDlg، وتدفع أحداث
// التسجيل/المكالمات/الرسائل إلى الواجهة عبر ExecuteScript.
// عقد الرسائل موثّق في web-shell/BRIDGE.md.
class MainShellDlg : public CBaseDialog
{
public:
	MainShellDlg(CWnd* pParent = NULL);
	~MainShellDlg();

	enum { IDD = IDD_MAIN_SHELL };

	void Restore();

	// دفع الأحداث من CmainDlg (تعمل على خيط الواجهة دائماً)
	void PushRegState(bool registered, const CString& message);
	void PushIncomingCall(const CString& number, const CString& name, pjsua_call_id call_id);
	void PushCallState(pjsua_call_id call_id, const CString& state, const CString& number, const CString& name);
	void PushMessage(const CString& from, const CString& text);
	void PushContacts();
	void PushAccount();
	void PushPinState(bool on);
	void PushSnapshot();

	bool IsWebViewReady() const { return m_webView != NULL && m_pageReady; }

protected:
	virtual void DoDataExchange(CDataExchange* pDX);
	virtual BOOL OnInitDialog();
	virtual void OnCancel() { ShowWindow(SW_HIDE); }
	virtual void OnOK() { ShowWindow(SW_HIDE); }
	virtual void TabFocusSet() {}
	virtual bool GotoTab(int i, CTabCtrl* tab = NULL) { return true; }
	afx_msg void OnSize(UINT nType, int cx, int cy);
	afx_msg void OnClose();
	afx_msg void OnSysCommand(UINT nID, LPARAM lParam);
	virtual void PostNcDestroy();
	afx_msg LRESULT OnShellReady(WPARAM wParam, LPARAM lParam);
	afx_msg LRESULT OnShellMessage(WPARAM wParam, LPARAM lParam);
	DECLARE_MESSAGE_MAP()

public:
	void* m_controller;
	void* m_webView;
	void* m_env;

private:
	bool m_pageReady;
	pjsua_call_id m_lastCallId;
	CString m_lastNumber;
	CString m_lastName;

	void InitWebView2();
	void ExecuteShellScript(const CString& js);
	void ProcessShellMessage(CString& message);
	pjsua_call_id ResolveCallId(int requested);

	static CString EscapeJs(const CString& input);
	static CString JsStringToCString(const Json::Value& value);
	static int JsInt(const Json::Value& value, int fallback);
};
