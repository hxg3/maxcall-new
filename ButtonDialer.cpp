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

#include "stdafx.h"
#include "ButtonDialer.h"
#include "Strsafe.h"
#include "const.h"
#include "define.h"
#include <gdiplus.h>
#pragma comment(lib, "gdiplus.lib")

using namespace Gdiplus;

CButtonDialer::CButtonDialer()
{
	forceNumeric = false;
	m_bHover = false;
	m_map.SetAt(_T("1"), _T(""));
	m_map.SetAt(_T("2"), _T("ABC"));
	m_map.SetAt(_T("3"), _T("DEF"));
	m_map.SetAt(_T("4"), _T("GHI"));
	m_map.SetAt(_T("5"), _T("JKL"));
	m_map.SetAt(_T("6"), _T("MNO"));
	m_map.SetAt(_T("7"), _T("PQRS"));
	m_map.SetAt(_T("8"), _T("TUV"));
	m_map.SetAt(_T("9"), _T("WXYZ"));
	m_map.SetAt(_T("0"), _T(""));
	m_map.SetAt(_T("*"), _T(""));
	m_map.SetAt(_T("#"), _T(""));
}

CButtonDialer::~CButtonDialer()
{
	CloseTheme();
}

BEGIN_MESSAGE_MAP(CButtonDialer, CButton)
	ON_WM_THEMECHANGED()
	ON_WM_MOUSEMOVE()
	ON_WM_MOUSELEAVE()
	ON_WM_SIZE()
END_MESSAGE_MAP()

void CButtonDialer::PreSubclassWindow()
{
	OpenTheme();

	HFONT hFont = (HFONT)GetStockObject(DEFAULT_GUI_FONT);
	LOGFONT lf;
	GetObject(hFont, sizeof(LOGFONT), &lf);
	lf.lfHeight = 10;
	lf.lfWeight = FW_NORMAL;
	CDC *pDC = GetDC();
	if (pDC) {
		dpiX = GetDeviceCaps(pDC->m_hDC, LOGPIXELSX);
		dpiY = GetDeviceCaps(pDC->m_hDC, LOGPIXELSY);
		lf.lfHeight = MulDiv(lf.lfHeight, dpiY, 96);
		ReleaseDC(pDC);
	}
	else {
		dpiX = dpiY = 96;
	}
	StringCchCopy(lf.lfFaceName, LF_FACESIZE, _T("Segoe UI"));
	m_FontLetters.CreateFontIndirect(&lf);

	DWORD dwStyle = ::GetClassLong(m_hWnd, GCL_STYLE);
	dwStyle &= ~CS_DBLCLKS;
	::SetClassLong(m_hWnd, GCL_STYLE, dwStyle);
}

LRESULT CButtonDialer::OnThemeChanged()
{
	CloseTheme();
	OpenTheme();
	return 0L;
}

void CButtonDialer::OnSize(UINT type, int w, int h)
{
	CButton::OnSize(type, w, h);
}

void CButtonDialer::OnMouseMove(UINT nFlags, CPoint point)
{
	if (!m_bHover) {
		m_bHover = true;
		TRACKMOUSEEVENT tme;
		tme.cbSize = sizeof(tme);
		tme.dwFlags = TME_LEAVE;
		tme.hwndTrack = m_hWnd;
		_TrackMouseEvent(&tme);
		Invalidate();
	}
	CButton::OnMouseMove(nFlags, point);
}

void CButtonDialer::OnMouseLeave()
{
	m_bHover = false;
	Invalidate();
	CButton::OnMouseLeave();
}

static GraphicsPath* CreateRoundedRect(Rect rect, INT radius)
{
	GraphicsPath* path = new GraphicsPath();
	int d = radius * 2;
	path->AddArc(rect.X, rect.Y, d, d, 180, 90);
	path->AddArc(rect.X + rect.Width - d, rect.Y, d, d, 270, 90);
	path->AddArc(rect.X + rect.Width - d, rect.Y + rect.Height - d, d, d, 0, 90);
	path->AddArc(rect.X, rect.Y + rect.Height - d, d, d, 90, 90);
	path->CloseFigure();
	return path;
}

void CButtonDialer::DrawItem(LPDRAWITEMSTRUCT lpDrawItemStruct)
{
	CDC dc;
	dc.Attach(lpDrawItemStruct->hDC);
	CRect rt(lpDrawItemStruct->rcItem);
	UINT state = lpDrawItemStruct->itemState;
	bool bPressed = (state & ODS_SELECTED) != 0;
	bool bFocused = (state & ODS_FOCUS) != 0;

	GdiplusStartupInput gdiplusStartupInput;
	ULONG_PTR gdiplusToken;
	GdiplusStartup(&gdiplusToken, &gdiplusStartupInput, NULL);

	{
		Graphics graphics(dc.m_hDC);
		graphics.SetSmoothingMode(SmoothingModeAntiAlias);
		graphics.SetTextRenderingHint(TextRenderingHintAntiAliasGridFit);

		Rect rect(rt.left, rt.top, rt.Width(), rt.Height());
		GraphicsPath* path = CreateRoundedRect(rect, 6);

		if (bPressed) {
			SolidBrush brushPressed(MAXCARE_TEAL_DARK);
			graphics.FillPath(&brushPressed, path);
		}
		else if (m_bHover) {
			SolidBrush brushHover(MAXCARE_TEAL_SOFT);
			graphics.FillPath(&brushHover, path);
		}
		else {
			SolidBrush brushNormal(MAXCARE_WHITE);
			graphics.FillPath(&brushNormal, path);
		}

		Pen penBorder(MAXCARE_BORDER, 1);
		graphics.DrawPath(&penBorder, path);

		delete path;

		CString strTemp;
		GetWindowText(strTemp);

		CString letters;
		COLORREF textColor;

		if (!forceNumeric && m_map.Lookup(strTemp, letters)) {
			if (bPressed) {
				textColor = MAXCARE_WHITE;
			}
			else {
				textColor = MAXCARE_TEXT;
			}
			dc.SetTextColor(textColor);
			dc.SetBkMode(TRANSPARENT);

			CRect rtl(rt);
			rtl.left += MulDiv(10, dpiX, 96);
			dc.DrawText(strTemp, rtl, DT_LEFT | DT_VCENTER | DT_SINGLELINE);

			HFONT hOldFont = (HFONT)SelectObject(dc.m_hDC, m_FontLetters);
			rtl.left += MulDiv(12, dpiX, 96);
			rtl.right -= MulDiv(4, dpiX, 96);
			dc.SetTextColor(bPressed ? RGB(221, 242, 240) : MAXCARE_TEXT_MUTED);
			dc.DrawText(letters, rtl, DT_LEFT | DT_VCENTER | DT_SINGLELINE);
			SelectObject(dc.m_hDC, hOldFont);
		}
		else {
			if (forceNumeric) {
				textColor = bPressed ? MAXCARE_WHITE : MAXCARE_TEXT;
			}
			else {
				textColor = bPressed ? MAXCARE_WHITE : MAXCARE_TEXT_MUTED;
			}
			dc.SetTextColor(textColor);
			dc.SetBkMode(TRANSPARENT);
			dc.DrawText(strTemp, rt, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
		}
	}

	GdiplusShutdown(gdiplusToken);
	dc.Detach();
}
