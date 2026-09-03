#pragma once

#include "resource.h"
#include "BaseDialog.h"
#include "json.h"

struct ICoreWebView2Controller;
struct ICoreWebView2;
struct ICoreWebView2Environment;

#define WM_LOGIN_WEB_MESSAGE (WM_USER + 112)
#define WM_LOGIN_WEB_RESULT (WM_USER + 113)

// LoginWebDlg — شاشة تسجيل الدخول الحديثة (WebView2).
// نفس منطق LoginDlg تماماً (POST إلى /api/agent/login ثم تعبئة
// accountSettings في الذاكرة) لكن الواجهة web-shell/login.html.
// عقد الرسائل في web-shell/LOGIN_BRIDGE.md.
class LoginWebDlg : public CBaseDialog
{
public:
	LoginWebDlg(CWnd* pParent = NULL);
	~LoginWebDlg();

	enum { IDD = IDD_LOGIN_WEB };

	bool loginSuccess;

	static CString EscapeLoginJsonPublic(const CString& value);

protected:
	virtual void DoDataExchange(CDataExchange* pDX);
	virtual BOOL OnInitDialog();
	virtual void OnCancel() { EndDialog(IDCANCEL); }
	virtual void OnOK() {}
	virtual void TabFocusSet() {}
	virtual bool GotoTab(int i, CTabCtrl* tab = NULL) { return true; }
	afx_msg void OnSize(UINT nType, int cx, int cy);
	afx_msg void OnClose();
	virtual void PostNcDestroy();
	afx_msg LRESULT OnLoginWebMessage(WPARAM wParam, LPARAM lParam);
	afx_msg LRESULT OnLoginWebResult(WPARAM wParam, LPARAM lParam);
	DECLARE_MESSAGE_MAP()

public:
	void* m_controller;
	void* m_webView;
	void* m_env;

private:
	void InitWebView2();
	void ExecuteLoginScript(const CString& js);
	void ProcessLoginMessage(CString& message);
	void PushLoginBusy(bool busy);
	void PushLoginError(const CString& message);

	static CString EscapeLoginJs(const CString& input);
	static CString LoginJsString(const Json::Value& value);
};
