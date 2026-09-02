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

#pragma once

#include "const.h"

#define STR_SZ 256

#define _GLOBAL_WIDTH 180

//#ifndef _GLOBAL_RINGIN_WIDTH
#define _GLOBAL_RINGIN_WIDTH 180
//#endif

#define _GLOBAL_DIALER_WIDTH 162

#define _GLOBAL_ICON "res\\maxcall.ico"
#define _GLOBAL_ICON_INACTIVE "res\\inactive.ico"

#define _GLOBAL_HEIGHT1 0
#define _GLOBAL_HEIGHT2 _GLOBAL_HEIGHT1+16

#define _GLOBAL_HEIGHT3 _GLOBAL_HEIGHT2+23

#define _GLOBAL_HEIGHT4 _GLOBAL_HEIGHT3

#define _GLOBAL_HEIGHT_FINAL _GLOBAL_HEIGHT4

#define _GLOBAL_HEIGHT 192 + _GLOBAL_HEIGHT_FINAL

#define _GLOBAL_TAB_WIDTH 47

#define _GLOBAL_ACCT_OFFSET_LEFT 0

#define IDD_CALLS_OFFSET_INITIAL _GLOBAL_HEIGHT - 17
#define IDD_CALLS_OFFSET_LISTVIEW1 IDD_CALLS_OFFSET_INITIAL
#define IDD_CALLS_OFFSET_LISTVIEW2 IDD_CALLS_OFFSET_LISTVIEW1

#define IDD_CALLS_OFFSET_LISTVIEW IDD_CALLS_OFFSET_LISTVIEW2+2


#define _GLOBAL_CODECS_ENABLED "PCMA/8000/1 PCMU/8000/1"

#define _GLOBAL_SETT_DENYINC_DEFAULT "button"
#define _GLOBAL_SETT_AA_DEFAULT "button"

#define _GLOBAL_BUSINESS_FEATURE "This feature is not available in the free version."
#define _GLOBAL_MENU_WEBSITE ""
#define _GLOBAL_MENU_HELP ""
#define _GLOBAL_HELP_WEBSITE ""

#define _GLOBAL_EC_DEFAULT "1"

#define _GLOBAL_NAME_NICE _GLOBAL_NAME

#define _GLOBAL_CALL_PICKUP "**"

#define _GLOBAL_SHORTCUTS
#define _GLOBAL_SHORTCUTS_QTY 8

#define MACRO_ENABLE_LOCAL_ACCOUNT (accountSettings.enableLocalAccount || !accountSettings.accountId)

#define MACRO_SILENT (accountSettings.silent || accountSettings.hidden)

#define _GLOBAL_SUBSCRIBE

#ifndef _GLOBAL_VIDEO
#endif

#define _GLOBAL_SETT_HIDDEN_VALUE "0"

#ifndef LVS_EX_AUTOSIZECOLUMNS
#define LVS_EX_AUTOSIZECOLUMNS 0x10000000
#endif

#ifndef _GLOBAL_VIDEO
#endif

#define _GLOBAL_DIALER_CALL_COLOR RGB(8, 123, 120)
#define _GLOBAL_DIALER_END_COLOR RGB(201, 74, 74)

// === MaxCare Hospital Brand Colors ===
#define MAXCARE_TEAL        RGB(8, 123, 120)      // #087B78 - Primary
#define MAXCARE_TEAL_DARK   RGB(7, 91, 89)        // #075B59 - Primary Dark
#define MAXCARE_TEAL_LIGHT  RGB(10, 157, 152)     // #0A9D98 - Primary Light
#define MAXCARE_GOLD        RGB(214, 166, 42)      // #D6A62A - Accent
#define MAXCARE_GOLD_SOFT   RGB(244, 231, 191)     // #F4E7BF - Accent Soft
#define MAXCARE_SURFACE     RGB(248, 250, 250)     // #F8FAFA - Background
#define MAXCARE_WHITE       RGB(255, 255, 255)     // #FFFFFF
#define MAXCARE_TEAL_SOFT   RGB(221, 242, 240)     // #DDF2F0 - Teal Background
#define MAXCARE_TEXT        RGB(31, 41, 41)         // #1F2929 - Primary Text
#define MAXCARE_TEXT_SEC    RGB(61, 75, 75)         // #3D4B4B - Secondary Text
#define MAXCARE_TEXT_MUTED  RGB(113, 128, 128)      // #718080 - Muted Text
#define MAXCARE_BORDER      RGB(231, 236, 236)      // #E7ECEC - Border
#define MAXCARE_SUCCESS     RGB(33, 138, 103)       // #218A67 - Success
#define MAXCARE_WARNING     RGB(212, 154, 36)       // #D49A24 - Warning
#define MAXCARE_ERROR       RGB(201, 74, 74)        // #C94A4A - Error
#define MAXCARE_INFO        RGB(47, 128, 168)       // #2F80A8 - Info
