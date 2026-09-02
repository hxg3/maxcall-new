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
#include "ButtonBottom.h"   
#include "define.h"

IMPLEMENT_DYNAMIC(CButtonBottom, CMFCButton)
CButtonBottom::CButtonBottom()   
{   
	m_nFlatStyle = CMFCButton::BUTTONSTYLE_NOBORDERS;
	m_bTransparent = false;
	m_bDrawFocus = FALSE;
}
   
CButtonBottom::~CButtonBottom()   
{   
}

BEGIN_MESSAGE_MAP(CButtonBottom, CMFCButton)   
	ON_WM_CREATE()
	ON_WM_ERASEBKGND()
END_MESSAGE_MAP()

int CButtonBottom::OnCreate(LPCREATESTRUCT lpCreateStruct)
{
	textColor = MAXCARE_TEXT_MUTED;
	faceColor = MAXCARE_WHITE;
	UpdateColor();
	return CMFCButton::OnCreate(lpCreateStruct);
}

BOOL CButtonBottom::OnEraseBkgnd(CDC* pDC)
{
	UpdateColor();
	return CMFCButton::OnEraseBkgnd(pDC);
}

void CButtonBottom::UpdateColor(int nCheck)
{
	if (nCheck == -1) {
		nCheck = GetCheck();
	}
	if (nCheck) {
		SetTextColor(MAXCARE_WHITE);
		SetFaceColor(MAXCARE_TEAL, true);
	} else {
		SetTextColor(textColor);
		SetFaceColor(faceColor);
	}
}

void CButtonBottom::SetCheck(int nCheck)
{
	CMFCButton::SetCheck(nCheck);
	UpdateColor(nCheck);
}
