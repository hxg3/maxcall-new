#pragma once

#include "resource.h"
#include "BaseDialog.h"

#define UM_LOGIN_RESULT (WM_USER + 9001)

class LoginDlg : public CBaseDialog
{
public:
	LoginDlg(CWnd* pParent = NULL);
	enum { IDD = IDD_LOGIN };

	CString m_username;
	CString m_password;
	bool loginSuccess;
	int m_loadingDots;

protected:
	virtual void DoDataExchange(CDataExchange* pDX);
	virtual BOOL OnInitDialog();
	virtual void TabFocusSet() {}
	virtual bool GotoTab(int i, CTabCtrl* tab = NULL) { return true; }
	DECLARE_MESSAGE_MAP()

public:
	afx_msg void OnBnClickedLogin();
	afx_msg void OnBnClickedCancel();
	afx_msg void OnClose();
	afx_msg void OnTimer(UINT_PTR nIDEvent);
};
