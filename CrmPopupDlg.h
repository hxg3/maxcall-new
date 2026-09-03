#pragma once

#include "resource.h"
#include "BaseDialog.h"
#include <pjsua-lib/pjsua.h>
#include "json.h"

struct ICoreWebView2Controller;
struct ICoreWebView2;
struct ICoreWebView2Environment;

#define WM_WEBVIEW_READY (WM_USER + 100)
#define WM_WEBVIEW_MESSAGE (WM_USER + 101)
#define WM_CRM_LOAD_RESULT (WM_USER + 102)
#define WM_CRM_SAVE_RESULT (WM_USER + 103)
#define WM_CRM_HISTORY (WM_USER + 115)

class CrmPopupDlg : public CBaseDialog
{
public:
	CrmPopupDlg(CWnd* pParent = NULL);
	~CrmPopupDlg();
	enum { IDD = IDD_CRM_POPUP };

	pjsua_call_id call_id;
	CString callerNumber;
	CString callerName;
	CString notes;

	void LoadCallerInfo();
	void FetchCallerHistory();
	void Restore();

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
	afx_msg LRESULT OnWebViewReady(WPARAM wParam, LPARAM lParam);
	afx_msg LRESULT OnWebViewMessage(WPARAM wParam, LPARAM lParam);
	afx_msg LRESULT OnCrmLoadResult(WPARAM wParam, LPARAM lParam);
	afx_msg LRESULT OnCrmSaveResult(WPARAM wParam, LPARAM lParam);
	afx_msg LRESULT OnCrmHistory(WPARAM wParam, LPARAM lParam);
	DECLARE_MESSAGE_MAP()

public:
	void* m_controller;
	void* m_webView;
	void* m_env;

private:
	void InitWebView2();
	void UpdateWebView();
	void ProcessWebViewMessage(CString& message);
	void SaveCallerInfo();

	static CString JsonStringToCString(const Json::Value& value);
	static CString EscapeJson(const CString& input);
	static CString UrlEncodeCallerNumber(const CString& number);
};
