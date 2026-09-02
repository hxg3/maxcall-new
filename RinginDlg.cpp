/*
 * Copyright (C) 2011-2024 MicroSIP (http://www.microsip.org)
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 */

#include "StdAfx.h"
#include "RinginDlg.h"
#include "langpack.h"
#include "mainDlg.h"
#include "settings.h"
#include <dwmapi.h>

RinginDlg::RinginDlg(CWnd* pParent /*=NULL*/)
	: CBaseDialog(RinginDlg::IDD, pParent)
{
	Create(IDD, pParent);
	answered = false;
}

RinginDlg::~RinginDlg(void)
{
}

int RinginDlg::OnCreate(LPCREATESTRUCT lpCreateStruct)
{
	if (langPack.rtl) {
		ModifyStyleEx(0, WS_EX_LAYOUTRTL);
	}
	return 0;
}

void RinginDlg::DoDataExchange(CDataExchange* pDX)
{
	CBaseDialog::DoDataExchange(pDX);
}

BOOL RinginDlg::OnInitDialog() {
	CBaseDialog::OnInitDialog();
	ModifyStyle(WS_SYSMENU, 0, SWP_FRAMECHANGED);

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

	AutoMove(IDC_ANSWER, 0, 100, 0, 0);
	AutoMove(IDC_DECLINE, 0, 100, 0, 0);

	TranslateDialog(this->m_hWnd);

	CFont* font = this->GetFont();
	LOGFONT lf;
	font->GetLogFont(&lf);

	lf.lfHeight = 24;
	lf.lfWeight = FW_BOLD;
	m_font.CreateFontIndirect(&lf);
	GetDlgItem(IDC_CALLER_NAME)->SetFont(&m_font);

	GetDlgItem(IDC_CALLER_NAME)->ModifyStyleEx(WS_EX_CLIENTEDGE, 0, SWP_NOSIZE | SWP_FRAMECHANGED);
	GetDlgItem(IDC_CALLER_ADDR)->ModifyStyleEx(WS_EX_CLIENTEDGE, 0, SWP_NOSIZE | SWP_FRAMECHANGED);

	int x, y;
	if (accountSettings.randomAnswerBox) {
		CRect ringinRect;
		GetWindowRect(&ringinRect);
		CRect primaryScreenRect;
		SystemParametersInfo(SPI_GETWORKAREA, 0, &primaryScreenRect, 0);
		x = primaryScreenRect.left + ((primaryScreenRect.right - primaryScreenRect.left) - ringinRect.Width()) * rand() / RAND_MAX;
		y = primaryScreenRect.top + ((primaryScreenRect.bottom - primaryScreenRect.top) - ringinRect.Height()) * rand() / RAND_MAX;
	}
	else {
		if (mainDlg->ringinDlgs.GetCount()) {
			CRect rect;
			mainDlg->ringinDlgs.GetAt(mainDlg->ringinDlgs.GetCount() - 1)->GetWindowRect(&rect);
			x = rect.left + 22;
			y = rect.top + 22;
		}
		else {
			if (accountSettings.ringinX || accountSettings.ringinY) {
				CRect screenRect;
				SystemParametersInfo(SPI_GETWORKAREA, 0, &screenRect, 0);
				CRect rect;
				GetWindowRect(&rect);
				int maxLeft = screenRect.right - rect.Width();
				x = accountSettings.ringinX > maxLeft ? maxLeft : (accountSettings.ringinX < screenRect.left ? screenRect.left : accountSettings.ringinX);
				int maxTop = screenRect.bottom - rect.Height();
				y = accountSettings.ringinY > maxTop ? maxTop : (accountSettings.ringinY < screenRect.top ? screenRect.top : accountSettings.ringinY);
			}
			else {
				CRect ringinRect;
				GetWindowRect(&ringinRect);
				CRect primaryScreenRect;
				SystemParametersInfo(SPI_GETWORKAREA, 0, &primaryScreenRect, 0);
				x = (primaryScreenRect.Width() - ringinRect.Width()) / 2;
				y = (primaryScreenRect.Height() - ringinRect.Height()) / 2;
			}
		}
	}
	SetWindowPos(accountSettings.bringToFrontOnIncoming || accountSettings.alwaysOnTop ? &this->wndTopMost : &this->wndNoTopMost, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE);
	if (accountSettings.bringToFrontOnIncoming) {
		if (!MACRO_SILENT) {
			if (mainDlg->IsWindowVisible()) {
				if (mainDlg->IsIconic()) {
					mainDlg->ShowWindow(SW_RESTORE);
				}
				else {
					mainDlg->ShowWindow(SW_HIDE);
					mainDlg->ShowWindow(SW_MINIMIZE);
					mainDlg->ShowWindow(SW_RESTORE);
				}
			}
			ShowWindow(SW_SHOWNORMAL);
			SetForegroundWindow();
		}
	}
	else {
		if (mainDlg->IsWindowVisible()) {
			ShowWindow(SW_SHOWNORMAL);
		}
	}
	return 0;
}

void RinginDlg::SetCallId(pjsua_call_id new_call_id)
{
	call_id = new_call_id;
}

BEGIN_MESSAGE_MAP(RinginDlg, CBaseDialog)
	ON_WM_CREATE()
	ON_WM_CLOSE()
	ON_WM_TIMER()
	ON_WM_MOVE()
	ON_WM_SHOWWINDOW()
	ON_BN_CLICKED(IDOK, &RinginDlg::OnBnClickedOk)
	ON_BN_CLICKED(IDCANCEL, &RinginDlg::OnBnClickedCancel)
	ON_BN_CLICKED(IDC_ANSWER, &RinginDlg::OnBnClickedAudio)
	ON_BN_CLICKED(IDC_DECLINE, &RinginDlg::OnBnClickedHide)
END_MESSAGE_MAP()

void RinginDlg::OnClose()
{
	OnBnClickedHide();
}

void RinginDlg::OnAnswer()
{
	answered = true;
	GetDlgItem(IDC_ANSWER)->EnableWindow(FALSE);
	GetDlgItem(IDC_DECLINE)->EnableWindow(FALSE);
}

void RinginDlg::Close(BOOL accept)
{
	int count = mainDlg->ringinDlgs.GetCount();
	for (int i = 0; i < count; i++)
	{
		if (call_id == mainDlg->ringinDlgs.GetAt(i)->call_id)
		{
			if (!accept) {
				mainDlg->UpdateWindowText(_T("-"));
			}
			if (count == 1) {
				mainDlg->PlayerStop();
			}
			mainDlg->ringinDlgs.RemoveAt(i);
			call_id = -1;
			break;
		}
	}
	if (call_id == -1) {
		DestroyWindow();
	}
}

void RinginDlg::OnBnClickedOk()
{
}

void RinginDlg::OnBnClickedCancel()
{
	Close();
}

void RinginDlg::OnBnClickedHide()
{
	ShowWindow(SW_HIDE);
}

void RinginDlg::OnBnClickedAudio()
{
	CallAccept();
}

void RinginDlg::CallAccept(BOOL hasVideo)
{
	if (!answered) {
		mainDlg->onCallAnswer((WPARAM)call_id, (LPARAM)hasVideo);
	}
}

void RinginDlg::OnShowWindow(BOOL bShow, UINT nStatus)
{
	SetTimer(IDT_TIMER_INIT_RINGIN, 1000, NULL);
}

void RinginDlg::OnTimer(UINT_PTR TimerVal)
{
	if (TimerVal == IDT_TIMER_INIT_RINGIN)
	{
		KillTimer(IDT_TIMER_INIT_RINGIN);
	}
}

void RinginDlg::Restore()
{
	if (!answered && call_id != PJSUA_INVALID_ID) {
		ShowWindow(SW_SHOWNORMAL);
		SetForegroundWindow();
	}
}

void RinginDlg::OnMove(int x, int y)
{
	if (IsWindowVisible() && !IsZoomed() && !IsIconic()) {
		CRect cRect;
		GetWindowRect(&cRect);
		accountSettings.ringinX = cRect.left;
		accountSettings.ringinY = cRect.top;
		mainDlg->AccountSettingsPendingSave();
	}
}
