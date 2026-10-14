/*
 * Copyright (C) 2011-2024 MicroSIP (http://www.microsip.org)
 *
 * This program is free software; you can redistribute it and/or modify
 * it unbodr the terms of the GNU General Public License as published by
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

// ---------------------------------------------------------------------------
// Version
//
// _GLOBAL_VERSION is the single source of truth for the product version.
// It must match the git tag that ships a release (e.g. tag v6.1.6 => 6.1.6).
// build.yml fails the release job when the tag and this value disagree, so a
// build can never claim a version the tag does not carry.
//
// CI also rewrites these macros from the tag at build time for tag builds, so
// a release binary always reports exactly the tag it was cut from.
// ---------------------------------------------------------------------------
#define _GLOBAL_VERSION "6.1.28"
#define _GLOBAL_VERSION_MAJOR 6
#define _GLOBAL_VERSION_MINOR 1
#define _GLOBAL_VERSION_PATCH 28
#define _GLOBAL_VERSION_COMMA 6,1,28,0

#define _GLOBAL_KEY "*********"
#define _GLOBAL_VIDEO 
#define _GLOBAL_NAME "MaxCall"

// ---------------------------------------------------------------------------
// Update channel infrastructure
//
// The download the user ever sees is a single MaxCall.exe. Everything the
// auto-updater needs is machine-only metadata, published to its own hidden
// releases so it never clutters the release page people actually visit:
//
//   stable manifest -> releases/download/manifest-stable/update.json
//   beta   manifest -> releases/download/manifest-beta/update.json
//
// Both are RSA-signed at build time and verified locally against
// UpdatePublicKey.h before anything is downloaded or executed.
// ---------------------------------------------------------------------------
#define _GLOBAL_UPDATE_REPO "hxg3/maxcall-new"
#define _GLOBAL_UPDATE_BASE_URL "https://github.com/" _GLOBAL_UPDATE_REPO "/releases"
#define _GLOBAL_UPDATE_MANIFEST "update.json"
#define _GLOBAL_UPDATE_SIGNATURE "update.json.sig"
#define _GLOBAL_UPDATE_STABLE_CHANNEL_TAG "manifest-stable"
#define _GLOBAL_UPDATE_BETA_CHANNEL_TAG "manifest-beta"
#define _GLOBAL_UPDATE_STABLE_URL _GLOBAL_UPDATE_BASE_URL "/download/" _GLOBAL_UPDATE_STABLE_CHANNEL_TAG "/" _GLOBAL_UPDATE_MANIFEST
#define _GLOBAL_UPDATE_BETA_URL _GLOBAL_UPDATE_BASE_URL "/download/" _GLOBAL_UPDATE_BETA_CHANNEL_TAG "/" _GLOBAL_UPDATE_MANIFEST
#define _GLOBAL_UPDATE_STABLE_SIG_URL _GLOBAL_UPDATE_BASE_URL "/download/" _GLOBAL_UPDATE_STABLE_CHANNEL_TAG "/" _GLOBAL_UPDATE_SIGNATURE
#define _GLOBAL_UPDATE_BETA_SIG_URL _GLOBAL_UPDATE_BASE_URL "/download/" _GLOBAL_UPDATE_BETA_CHANNEL_TAG "/" _GLOBAL_UPDATE_SIGNATURE
// Periodic auto-check removed: it only produced noise on networks that cannot reach the update host. The user now checks manually via the tray/menu.
// #define _GLOBAL_UPDATE_CHECK_INTERVAL_HOURS 6

// Where "Check for Updates" sends the user when this machine cannot reach the
// update channel. The browser is not subject to the same TLS restrictions as
// the application, so this always works.
#define _GLOBAL_UPDATE_RELEASES_PAGE "https://github.com/" _GLOBAL_UPDATE_REPO "/releases/latest"

// A mirror served from the company's own network, tried automatically when
// github.com cannot be reached. Set it to "" to disable. Keeping this in the
// build is deliberate: users must never have to edit a file to receive
// updates. Integrity is unaffected because the manifest is still verified
// against the key embedded below, not against whoever served the file.
#define _GLOBAL_UPDATE_MIRROR_URL "http://maxcall.local:3001/updates" 
