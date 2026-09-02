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
#include "ButtonEx.h"   
#include "define.h"

IMPLEMENT_DYNAMIC(CButtonEx, CMFCButton)
CButtonEx::CButtonEx()   
{   
	m_nFlatStyle = CMFCButton::BUTTONSTYLE_NOBORDERS;
	m_bTransparent = false;
}
   
CButtonEx::~CButtonEx()   
{   
}

BEGIN_MESSAGE_MAP(CButtonEx, CMFCButton)   
END_MESSAGE_MAP()

BOOL CButtonEx::EnableWindow(BOOL bEnable)
{
	if (bEnable) {
		SetTextColor(m_TextColor);
		SetFaceColor(m_FaceColor, true);
	}
	else {
		SetTextColor(MAXCARE_TEXT_MUTED);
		SetFaceColor(MAXCARE_BORDER, true);
	}
	return CMFCButton::EnableWindow(bEnable);
}
