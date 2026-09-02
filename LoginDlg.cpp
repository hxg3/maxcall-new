#include "StdAfx.h"
#include "LoginDlg.h"
#include "langpack.h"
#include "mainDlg.h"
#include "settings.h"
#include "global.h"
#include "microsip.h"
#include "lib/utf.h"
#include "json.h"
#include "define.h"
#include <dwmapi.h>

static CString EscapeLoginJson(const CString& value)
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

LoginDlg::LoginDlg(CWnd* pParent)
	: CBaseDialog(LoginDlg::IDD, pParent)
{
	loginSuccess = false;
	m_loadingDots = 0;
}

void LoginDlg::DoDataExchange(CDataExchange* pDX)
{
	CBaseDialog::DoDataExchange(pDX);
	DDX_Text(pDX, IDC_LOGIN_USERNAME, m_username);
	DDX_Text(pDX, IDC_LOGIN_PASSWORD, m_password);
}

BEGIN_MESSAGE_MAP(LoginDlg, CBaseDialog)
	ON_BN_CLICKED(IDC_LOGIN_BTN, &LoginDlg::OnBnClickedLogin)
	ON_BN_CLICKED(IDCANCEL, &LoginDlg::OnBnClickedCancel)
	ON_WM_CLOSE()
	ON_WM_TIMER()
END_MESSAGE_MAP()

BOOL LoginDlg::OnInitDialog()
{
	CBaseDialog::OnInitDialog();

	TranslateDialog(this->m_hWnd);

	HICON hIcon = theApp.LoadIcon(IDR_MAINFRAME);
	SetIcon(hIcon, TRUE);
	SetIcon(hIcon, FALSE);

	BOOL darkMode = TRUE;
	DwmSetWindowAttribute(m_hWnd, DWMWA_USE_IMMERSIVE_DARK_MODE, &darkMode, sizeof(darkMode));
	COLORREF titleColor = MAXCARE_TEAL;
	DwmSetWindowAttribute(m_hWnd, DWMWA_CAPTION_COLOR, &titleColor, sizeof(titleColor));
	COLORREF txtColor = MAXCARE_WHITE;
	DwmSetWindowAttribute(m_hWnd, DWMWA_TEXT_COLOR, &txtColor, sizeof(txtColor));

	m_username = _T("");
	m_password = _T("");

	UpdateData(FALSE);

	GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(_T(""));
	GetDlgItem(IDC_LOGIN_USERNAME)->SetFocus();

	return FALSE;
}

void LoginDlg::OnBnClickedLogin()
{
	UpdateData(TRUE);

	if (m_username.IsEmpty() || m_password.IsEmpty()) {
		AfxMessageBox(Translate(_T("Please enter username and password.")), MB_ICONWARNING);
		return;
	}

	GetDlgItem(IDC_LOGIN_BTN)->EnableWindow(FALSE);
	GetDlgItem(IDCANCEL)->EnableWindow(FALSE);
	m_loadingDots = 0;
	SetTimer(1, 400, NULL);

	GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(Translate(_T("Connecting...")));

	CString jsonData;
	jsonData.Format(_T("{\"username\":\"%s\",\"password\":\"%s\"}"),
		(LPCTSTR)EscapeLoginJson(m_username),
		(LPCTSTR)EscapeLoginJson(m_password));

	CString url = _T("http://maxcare.local:3001/api/agent/login");
	CString headers = _T("Content-Type: application/json");

	URLGetAsyncData result = URLGetSync(url, true, jsonData, headers);

	KillTimer(1);
	GetDlgItem(IDC_LOGIN_BTN)->EnableWindow(TRUE);
	GetDlgItem(IDCANCEL)->EnableWindow(TRUE);

	if (result.statusCode == 200) {
		Json::Value response;
		Json::Reader reader;
		bool parsed = reader.parse((LPCSTR)CStringA(result.body), response);

		if (parsed && response.get("ok", false).asBool()) {
			GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(Translate(_T("Login successful!")));

			Json::Value extension = response["extension"];
			CString extVal;
			if (extension.isString()) {
				extVal = MSIP::Utf8DecodeUni(extension.asCString());
			}
			else if (extension.isInt() || extension.isUInt()) {
				extVal.Format(_T("%d"), extension.asInt());
			}
			if (extVal.IsEmpty()) {
				AfxMessageBox(Translate(_T("The account has no assigned extension.")), MB_ICONERROR);
				GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(_T(""));
				return;
			}

			Json::Value displayName = response["name"];
			CString nameVal = displayName.isString() ? MSIP::Utf8DecodeUni(displayName.asCString()) : _T("");

			Json::Value sipServer = response["sip_server"];
			CString sipServerVal = sipServer.isString() && !sipServer.asString().empty()
				? MSIP::Utf8DecodeUni(sipServer.asCString())
				: _T("maxcare.local");

			accountSettings.AccountDelete(1);

			accountSettings.account.server = sipServerVal;
			accountSettings.account.port = 5060;
			accountSettings.account.username = extVal;
			accountSettings.account.password = m_password;
			accountSettings.account.authID = extVal;
			accountSettings.account.displayName = nameVal.IsEmpty() ? m_username : nameVal;
			accountSettings.account.domain = sipServerVal;
			accountSettings.account.rememberPassword = false;
			accountSettings.account.transport = _T("udp");
			accountSettings.accountId = 1;

			loginSuccess = true;
			EndDialog(IDOK);
			return;
		}
		else {
			GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(Translate(_T("Invalid username or password.")));
		}
	}
	else {
		GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(Translate(_T("Connection failed. Check server address.")));
	}
}

void LoginDlg::OnBnClickedCancel()
{
	EndDialog(IDCANCEL);
}

void LoginDlg::OnClose()
{
	EndDialog(IDCANCEL);
}

void LoginDlg::OnTimer(UINT_PTR nIDEvent)
{
	if (nIDEvent == 1) {
		m_loadingDots = (m_loadingDots + 1) % 4;
		CString dots;
		for (int i = 0; i < m_loadingDots; i++) dots += _T(".");
		CString status;
		status.Format(_T("Connecting%s"), dots);
		GetDlgItem(IDC_LOGIN_STATUS)->SetWindowText(status);
	}
	CBaseDialog::OnTimer(nIDEvent);
}
