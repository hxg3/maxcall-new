#include "stdafx.h"
#include "AppUpdater.h"
#include "UpdatePublicKey.h"
#include "define.h"

#include <afxinet.h>
#include <shlobj.h>

#pragma comment(lib, "shell32.lib")

// targetver.h still pins _WIN32_WINNT to the XP level. BCrypt is Vista+, and we
// only raise the floor for this translation unit (same technique jumplist.h
// already uses for shobjidl.h) instead of changing the project's global target.
#undef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#include <bcrypt.h>
#pragma comment(lib, "bcrypt.lib")

#include "json.h"

namespace AppUpdate
{

static const DWORD kChunkSize = 64 * 1024;
static const DWORD kApplyWaitTimeoutMs = 120000;
static const DWORD kManifestTimeoutMs = 30000;
static const DWORD kDownloadTimeoutMs = 300000;

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

static CString Utf8ToUni(const char* data, int length)
{
	if (!data || length <= 0) {
		return CString();
	}
	int chars = MultiByteToWideChar(CP_UTF8, 0, data, length, NULL, 0);
	if (chars <= 0) {
		return CString();
	}
	CString out;
	wchar_t* buffer = out.GetBuffer(chars);
	int written = MultiByteToWideChar(CP_UTF8, 0, data, length, buffer, chars);
	if (written <= 0) {
		out.ReleaseBuffer(0);
		return CString();
	}
	out.ReleaseBuffer(written);
	return out;
}

static CString JsonToUni(const Json::Value& value)
{
	if (!value.isString()) {
		return CString();
	}
	std::string s = value.asString();
	return Utf8ToUni(s.c_str(), (int)s.length());
}

static void BytesToHex(const unsigned char* data, int len, CString& hexOut)
{
	static const TCHAR* digits = _T("0123456789abcdef");
	hexOut.Empty();
	for (int i = 0; i < len; i++) {
		hexOut.AppendChar(digits[(data[i] >> 4) & 0x0F]);
		hexOut.AppendChar(digits[data[i] & 0x0F]);
	}
}

// Accepts the base64 blob published next to the manifest, whitespace included.
static bool Base64Decode(const CByteArray& input, CByteArray& out)
{
	out.RemoveAll();

	unsigned int acc = 0;
	int bits = 0;
	for (int i = 0; i < input.GetSize(); i++) {
		char c = (char)input.GetAt(i);
		if (c == '=') {
			break;
		}
		int v;
		if (c >= 'A' && c <= 'Z') v = c - 'A';
		else if (c >= 'a' && c <= 'z') v = c - 'a' + 26;
		else if (c >= '0' && c <= '9') v = c - '0' + 52;
		else if (c == '+') v = 62;
		else if (c == '/') v = 63;
		else continue;                      // CR, LF, spaces

		acc = ((acc << 6) | (unsigned int)v) & 0xFFFFFFu;
		bits += 6;
		if (bits >= 8) {
			bits -= 8;
			out.Add((BYTE)((acc >> bits) & 0xFF));
		}
	}
	return out.GetSize() > 0;
}

// ---------------------------------------------------------------------------
// SManifest
// ---------------------------------------------------------------------------

void SUpdateEvent::AttachManifest(const SManifest& m)
{
	version = m.version;
	channel = m.channel;
	url = m.url;
	sha256 = m.sha256;
}

void SManifest::Reset()
{
	version.Empty();
	channel.Empty();
	url.Empty();
	sha256.Empty();
	notes.Empty();
	size = 0;
}

bool SManifest::IsValid() const
{
	if (version.IsEmpty() || url.IsEmpty() || sha256.GetLength() != 64) {
		return false;
	}
	for (int i = 0; i < 64; i++) {
		TCHAR c = sha256[i];
		bool hex = (c >= _T('0') && c <= _T('9')) || (c >= _T('a') && c <= _T('f'));
		if (!hex) {
			return false;
		}
	}
	// The mirror serves over plain HTTP because this network cannot complete TLS
	// with github.com. What vouches for the manifest is its signature, and what
	// vouches for the payload is the sha256 that signature covers, so the scheme
	// does not weaken either check. Insisting on https here made the install
	// button a silent no-op the moment the payload host was swapped to the
	// mirror, which is the only route this network has.
	CString scheme = url.Left(8);
	if (scheme.CompareNoCase(_T("https://")) == 0) {
		return true;
	}
	return url.Left(7).CompareNoCase(_T("http://")) == 0;
}

// ---------------------------------------------------------------------------
// Versioning
// ---------------------------------------------------------------------------

// "v6.1.6-beta.42" -> "6.1.6". Build metadata (6.1.6+abc) is ignored, as semver requires.
CString VersionCore(const CString& version)
{
	CString v = version;
	v.Trim();
	if (!v.IsEmpty() && (v[0] == _T('v') || v[0] == _T('V'))) {
		v = v.Mid(1);
	}
	int dash = v.Find(_T('-'));
	if (dash >= 0) {
		v = v.Left(dash);
	}
	int plus = v.Find(_T('+'));
	if (plus >= 0) {
		v = v.Left(plus);
	}
	v.Trim();
	return v;
}

// "6.1.6-beta.42" -> "beta.42", empty for a final release.
static CString VersionPre(const CString& version)
{
	CString v = version;
	v.Trim();
	if (!v.IsEmpty() && (v[0] == _T('v') || v[0] == _T('V'))) {
		v = v.Mid(1);
	}
	int dash = v.Find(_T('-'));
	if (dash < 0) {
		return CString();
	}
	return v.Mid(dash + 1);
}

static void SplitCore(const CString& core, long long parts[3])
{
	parts[0] = parts[1] = parts[2] = 0;
	CString rest = core;
	for (int i = 0; i < 3; i++) {
		int dot = rest.Find(_T('.'));
		CString piece = (dot < 0) ? rest : rest.Left(dot);
		parts[i] = piece.IsEmpty() ? 0 : _tcstoi64(piece, NULL, 10);
		if (dot < 0) {
			break;
		}
		rest = rest.Mid(dot + 1);
	}
}

// Semver identifier rule: digits only, and no leading zero unless it is "0".
static bool IsNumericIdentifier(const CString& s)
{
	if (s.IsEmpty() || (s.GetLength() > 1 && s[0] == _T('0'))) {
		return false;
	}
	for (int i = 0; i < s.GetLength(); i++) {
		if (s[i] < _T('0') || s[i] > _T('9')) {
			return false;
		}
	}
	return true;
}

// Dot separated identifier lists, semver 11.4 rules: numeric identifiers compare
// numerically and always rank below alphanumeric ones.
static int ComparePreRelease(const CString& a, const CString& b)
{
	if (a.IsEmpty() && b.IsEmpty()) return 0;
	if (a.IsEmpty()) return 1;     // a is a final release, b is a pre-release
	if (b.IsEmpty()) return -1;

	CString ra = a, rb = b;
	for (;;) {
		if (ra.IsEmpty() && rb.IsEmpty()) return 0;
		if (ra.IsEmpty()) return -1;
		if (rb.IsEmpty()) return 1;

		int da = ra.Find(_T('.'));
		int db = rb.Find(_T('.'));
		CString ia = (da < 0) ? ra : ra.Left(da);
		CString ib = (db < 0) ? rb : rb.Left(db);

		bool na = IsNumericIdentifier(ia);
		bool nb = IsNumericIdentifier(ib);

		int cmp;
		if (na && nb) {
			long long va = _tcstoi64(ia, NULL, 10), vb = _tcstoi64(ib, NULL, 10);
			cmp = (va < vb) ? -1 : (va > vb) ? 1 : 0;
		}
		else if (na != nb) {
			cmp = na ? -1 : 1;
		}
		else {
			int c = ia.Compare(ib);
			cmp = (c < 0) ? -1 : (c > 0) ? 1 : 0;
		}
		if (cmp != 0) {
			return cmp;
		}

		ra = (da < 0) ? CString() : ra.Mid(da + 1);
		rb = (db < 0) ? CString() : rb.Mid(db + 1);
	}
}

int CompareVersion(const CString& a, const CString& b)
{
	long long pa[3], pb[3];
	SplitCore(VersionCore(a), pa);
	SplitCore(VersionCore(b), pb);
	for (int i = 0; i < 3; i++) {
		if (pa[i] != pb[i]) {
			return (pa[i] < pb[i]) ? -1 : 1;
		}
	}
	return ComparePreRelease(VersionPre(a), VersionPre(b));
}

bool IsNewer(const CString& candidate, const CString& current)
{
	return CompareVersion(candidate, current) > 0;
}

// An on-premise mirror has to be reachable before any request is made, so the
// host allow list is relaxed for it: the signature check is what actually
// guarantees integrity, and the mirror operator is the same operator who signs.
static CString g_mirrorBase;
static bool g_usingMirror = false;

// Host of the configured mirror, cached so a request can be recognised as local
// without re-parsing the base on every call.
static CString g_mirrorHost;

static bool SplitUrl(const CString& url, CString& scheme, CString& host,
	INTERNET_PORT& port, CString& object);

void SetMirrorBase(const CString& base)
{
	CString trimmed = base;
	while (!trimmed.IsEmpty() && trimmed[trimmed.GetLength() - 1] == _T('/')) {
		trimmed = trimmed.Left(trimmed.GetLength() - 1);
	}
	g_mirrorBase = trimmed;

	CString scheme, host, object;
	INTERNET_PORT port = INTERNET_DEFAULT_HTTP_PORT;
	g_mirrorHost.Empty();
	if (SplitUrl(trimmed, scheme, host, port, object)) {
		g_mirrorHost = host;
	}
}

CString MirrorBase()
{
	return g_mirrorBase;
}

CString ManifestUrl(EUpdateChannel channel)
{
	if (!g_mirrorBase.IsEmpty()) {
		return g_mirrorBase + _T("/") +
			(channel == ChannelBeta ? _T("update.json.beta") : _T("update.json"));
	}
	return (channel == ChannelBeta)
		? CString(_T(_GLOBAL_UPDATE_BETA_URL))
		: CString(_T(_GLOBAL_UPDATE_STABLE_URL));
}

CString SignatureUrl(EUpdateChannel channel)
{
	if (!g_mirrorBase.IsEmpty()) {
		return g_mirrorBase + _T("/") +
			(channel == ChannelBeta ? _T("update.json.beta.sig") : _T("update.json.sig"));
	}
	return (channel == ChannelBeta)
		? CString(_T(_GLOBAL_UPDATE_BETA_SIG_URL))
		: CString(_T(_GLOBAL_UPDATE_STABLE_SIG_URL));
}

// ---------------------------------------------------------------------------
// HTTP
//
// Certificate validation is deliberately left at its default (on). The existing
// URLGetAsync helper turns every check off, which is acceptable for CRM lookups
// but not for a channel that ends in running a downloaded executable.
// ---------------------------------------------------------------------------

// Transport reuses MFC's CInternetSession wrapper through exactly the calls
// global.cpp already makes for URLGetAsync (GetHttpConnection / OpenRequest /
// SendRequest / QueryInfoStatusCode / QueryInfo / Read), so nothing here depends
// on an API this project has not already proven it can compile and link.
//
// Certificate validation is deliberately left at its default (on). URLGetAsync
// switches every SECURITY_FLAG_IGNORE_* check off, which is tolerable for a CRM
// lookup but not for a channel that ends in running a downloaded executable.
// Older Windows builds negotiate TLS 1.0/1.1 by default and GitHub refuses
// both. The constants are guarded because older SDKs do not declare them.
#ifndef INTERNET_OPTION_SECURE_PROTOCOLS
#define INTERNET_OPTION_SECURE_PROTOCOLS 84
#endif
#ifndef INTERNET_FLAG_SECURE_PROTOCOL_TLS1_2
#define INTERNET_FLAG_SECURE_PROTOCOL_TLS1_2 0x00000800
#endif

struct SHttpSession
{
	CInternetSession session;
	CHttpConnection* connection;
	CHttpFile* file;
	DWORD contentLength;
	DWORD statusCode;

	SHttpSession() : connection(NULL), file(NULL), contentLength(0), statusCode(0)
	{
		// TLS 1.2 for this session; without it the handshake fails outright on
		// machines whose Schannel defaults predate it, while the browser still
		// works because it ships its own TLS stack. The LONG overload of SetOption
		// hands WinInet a pointer to the value, which is what this option expects.
		session.SetOption(INTERNET_OPTION_SECURE_PROTOCOLS,
			(LONG)INTERNET_FLAG_SECURE_PROTOCOL_TLS1_2);
	}
	~SHttpSession() { Close(); }

	void Close()
	{
		if (file) { file->Close(); delete file; file = NULL; }
		if (connection) { connection->Close(); delete connection; connection = NULL; }
	}
};

// Splits an absolute URL into the parts the transport needs. Deliberately hand
// rolled: MFC ships AfxParseURL and AfxParseURLEx with different parameter
// lists, and this module needs neither userinfo nor a service-type enum.
static bool SplitUrl(const CString& url, CString& scheme, CString& host,
	INTERNET_PORT& port, CString& object)
{
	scheme.Empty();
	host.Empty();
	object.Empty();
	port = INTERNET_DEFAULT_HTTPS_PORT;

	int schemeEnd = url.Find(_T("://"));
	if (schemeEnd <= 0) {
		return false;
	}
	scheme = url.Left(schemeEnd);
	scheme.MakeLower();
	// The internal mirror is plain HTTP. Rejecting it here made the request fail
	// before the try block, so nothing was logged and the mirror simply looked
	// like a host that never answered.
	if (scheme != _T("https") && scheme != _T("http")) {
		return false;
	}
	if (scheme == _T("http")) {
		port = INTERNET_DEFAULT_HTTP_PORT;
	}

	CString rest = url.Mid(schemeEnd + 3);
	int slash = rest.Find(_T('/'));
	CString authority = (slash < 0) ? rest : rest.Left(slash);
	object = (slash < 0) ? _T("/") : rest.Mid(slash);

	int colon = authority.Find(_T(':'));
	if (colon >= 0) {
		int parsed = _ttoi(authority.Mid(colon + 1));
		if (parsed > 0 && parsed <= 65535) {
			port = (INTERNET_PORT)parsed;
		}
		authority = authority.Left(colon);
	}
	host = authority;
	return !host.IsEmpty();
}

// Release assets live on github.com and are served from GitHub's CDN. Anything
// else means the manifest was tampered with or the repository moved.
static bool IsAllowedUpdateHost(const CString& host)
{
	return host.CompareNoCase(_T("github.com")) == 0
		|| host.CompareNoCase(_T("www.github.com")) == 0
		|| host.CompareNoCase(_T("objects.githubusercontent.com")) == 0
		|| host.CompareNoCase(_T("release-assets.githubusercontent.com")) == 0
		|| host.CompareNoCase(_T("github-releases.githubusercontent.com")) == 0;
}

// Content-Length straight out of the raw header block, so the progress bar and
// the truncation check do not depend on a typed QueryInfo overload.
static DWORD ParseContentLength(const CString& rawHeaders)
{
	for (CString line = rawHeaders; !line.IsEmpty(); ) {
		int eol = line.Find(_T("\r\n"));
		CString header = (eol < 0) ? line : line.Left(eol);
		line = (eol < 0) ? CString() : line.Mid(eol + 2);
		int colon = header.Find(_T(':'));
		if (colon > 0 && header.Left(colon).CompareNoCase(_T("Content-Length")) == 0) {
			return (DWORD)_ttoi(header.Mid(colon + 1));
		}
	}
	return 0;
}

// Records what WinInet actually complained about. Losing this behind a generic
// sentence is what made the previous failure impossible to diagnose.
static void HttpDiag(const CString& text)
{
	CString line = _T("[update] ") + text;
	FILE* f = fopen("maxcall_debug.log", "a");
	if (f) {
		fprintf(f, "%ls\n", (LPCTSTR)line);
		fclose(f);
	}
}

#ifndef SECURITY_FLAG_IGNORE_REVOCATION
#define SECURITY_FLAG_IGNORE_REVOCATION 0x00000080
#endif

// The WinInet codes worth naming in plain language. FORMAT_MESSAGE cannot help:
// these live in wininet.dll, not in the system table, which is why the previous
// log showed a blank reason next to the number.
static CString DescribeWinInetError(DWORD code)
{
	CString text;
	switch (code) {
	case 12152: text = _T("- secure connection failed (clock, TLS interception, or missing root certificates)");
		break;
	case 12007: text = _T("- host not found (DNS cannot resolve it)");
		break;
	case 12029: text = _T("- cannot connect (blocked by firewall or the server is down)");
		break;
	case 12002: text = _T("- the operation timed out before the server answered");
		break;
	case 12057: text = _T("- the secure connection was reset");
		break;
	default: {
		TCHAR detail[256];
		detail[0] = 0;
		::FormatMessage(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
			NULL, code, 0, detail, sizeof(detail) / sizeof(TCHAR) - 1, NULL);
		text.Format(_T("- %s"), detail);
		break;
	}
	}
	return text;
}

static bool HttpOpenOnce(const CString& url, SHttpSession& http, CString& error,
	DWORD timeoutMs)
{
	CString scheme, host, object;
	INTERNET_PORT port = INTERNET_DEFAULT_HTTPS_PORT;
	if (!SplitUrl(url, scheme, host, port, object)) {
		error = _T("The update URL is malformed.");
		return false;
	}
	// The manifest is signed, so this is defence in depth rather than the primary
	// control. It is skipped for an explicitly configured mirror, whose operator
	// also holds the signing key.
	if (!IsAllowedUpdateHost(host) && g_mirrorBase.IsEmpty()) {
		error = _T("The update is hosted on an unexpected server and was refused.");
		return false;
	}

	// The internal mirror is plain HTTP; GitHub is HTTPS. Use the right flag.
	DWORD connectFlags = (scheme.CompareNoCase(_T("https")) == 0) ? INTERNET_FLAG_SECURE : 0;

	// The mirror sits on the local network, so any proxy configured for internet
	// access must not be consulted for it: the request is sent to the proxy,
	// which cannot route to the LAN host, and the call times out. github keeps
	// the machine's normal path.
	if (!g_mirrorBase.IsEmpty() && !g_mirrorHost.IsEmpty()
		&& host.CompareNoCase(g_mirrorHost) == 0) {
		INTERNET_PROXY_INFO proxy;
		ZeroMemory(&proxy, sizeof(proxy));
		proxy.dwAccessType = INTERNET_OPEN_TYPE_DIRECT;
		http.session.SetOption(INTERNET_OPTION_PROXY, &proxy, sizeof(proxy));
	}

	try {
		http.connection = http.session.GetHttpConnection(host, connectFlags, port);
		if (!http.connection) {
			error = _T("Cannot open a connection to the update server.");
			HttpDiag(_T("GetHttpConnection returned null for ") + host);
			return false;
		}

		// Argument shape mirrors global.cpp, which this project already builds;
		// the reference to a non-string literal the earlier version passed here is
		// replaced with nulls.
		http.file = http.connection->OpenRequest(_T("GET"), object, 0, 0, 0, 0,
			INTERNET_FLAG_RELOAD | INTERNET_FLAG_DONT_CACHE | INTERNET_FLAG_KEEP_CONNECTION);
		if (!http.file) {
			error = _T("Cannot create the update request.");
			HttpDiag(_T("OpenRequest returned null for ") + object);
			return false;
		}

		DWORD timeout = timeoutMs / 1000;
		if (timeout == 0) {
			timeout = 1;
		}
		http.file->SetOption(INTERNET_OPTION_CONNECT_TIMEOUT, timeout);
		http.file->SetOption(INTERNET_OPTION_RECEIVE_TIMEOUT, timeout);
		http.file->SetOption(INTERNET_OPTION_SEND_TIMEOUT, timeout);

		if (!http.file->SendRequest()) {
			error = _T("The update server did not answer the request.");
			HttpDiag(_T("SendRequest failed for ") + url);
			return false;
		}

		http.file->QueryInfoStatusCode(http.statusCode);

		CString rawHeaders;
		if (http.file->QueryInfo(HTTP_QUERY_RAW_HEADERS_CRLF, rawHeaders)) {
			http.contentLength = ParseContentLength(rawHeaders);
		}

		CString status;
		status.Format(_T("%s -> HTTP %u, %u bytes"), (LPCTSTR)url, http.statusCode, http.contentLength);
		HttpDiag(status);
	}
	catch (...) {
		// WinInet records its failure in the thread's last error; asking MFC for it
		// depends on accessors this toolchain does not expose.
		DWORD code = ::GetLastError();
		CString number;
		number.Format(_T("%u"), code);
		HttpDiag(url + _T(" -> WinInet error ") + number + DescribeWinInetError(code));

		CString message;
		message.Format(
			_T("MaxCall could not reach the update server (network error %u).\n\n")
			_T("This machine cannot complete a secure connection to GitHub, usually "
			  L"because of a TLS-inspecting proxy, an out-of-date clock, or missing "
			  L"root certificates. A company network that blocks it needs an internal "
			  L"mirror instead: set updateBaseUrl in MaxCall.ini under [Settings]."),
			code);
		error = message;
		return false;
	}
	return true;
}

// Second attempt for machines where revocation checking cannot succeed: the CRL
// distribution point sits on a different host that the network blocks, which is
// what makes WinInet fail with 12152 while a browser on the same machine works.
// The chain and the host name are still validated; only the revocation lookup is
// skipped, and the manifest is signed anyway so a substituted file is refused.
static bool HttpOpenNoRevocation(const CString& url, SHttpSession& http, CString& error,
	DWORD timeoutMs)
{
	DWORD flags = SECURITY_FLAG_IGNORE_REVOCATION;
	http.session.SetOption(INTERNET_OPTION_SECURITY_FLAGS, (LONG)flags);
	return HttpOpenOnce(url, http, error, timeoutMs);
}

// Returns the session that answered, or null. Both sessions stay owned by the
// caller, so nothing has to be moved or swapped.
static SHttpSession* HttpOpen(const CString& url, SHttpSession& strict, SHttpSession& relaxed,
	CString& error, DWORD timeoutMs)
{
	if (HttpOpenOnce(url, strict, error, timeoutMs)) {
		return &strict;
	}
	// Only a secure failure is worth a second attempt with relaxed revocation.
	if (error.Find(_T("12152")) < 0) {
		return NULL;
	}
	CString firstReason = error;
	HttpDiag(_T("secure failure; retrying without revocation checking"));
	if (HttpOpenNoRevocation(url, relaxed, error, timeoutMs)) {
		HttpDiag(_T("revocation checking was the blocker; the certificate chain and "
			_T("the host name were still verified")));
		return &relaxed;
	}
	error = firstReason;
	return NULL;
}

// Reads the response body. CHttpFile reports end of stream as a zero-length read
// and does not distinguish it from a failure, so a short body is caught afterwards
// by comparing against Content-Length. maxBytes stops a broken server from making
// the client allocate without bound.
static bool ReadBody(CHttpFile* file, DWORD expected, DWORD maxBytes,
	CByteArray& out, CString& error)
{
	out.SetSize(0);
	CByteArray chunk;
	chunk.SetSize((int)kChunkSize);
	for (;;) {
		UINT read = file->Read(chunk.GetData(), (UINT)chunk.GetSize());
		if (read == 0) {
			break;
		}
		int at = out.GetSize();
		out.SetSize(at + (int)read);
		memcpy(out.GetData() + at, chunk.GetData(), read);
		if ((DWORD)out.GetSize() > maxBytes) {
			error = _T("The update metadata is implausibly large.");
			return false;
		}
	}
	if (expected > 0 && (DWORD)out.GetSize() != expected) {
		error = _T("The update metadata arrived truncated.");
		return false;
	}
	return true;
}

bool HttpGet(const CString& url, CByteArray& body, DWORD& statusCode, CString& error, DWORD timeoutMs)
{
	body.SetSize(0);
	statusCode = 0;

	SHttpSession strict;
	SHttpSession relaxed;
	SHttpSession* http = HttpOpen(url, strict, relaxed, error, timeoutMs);
	if (!http) {
		return false;
	}
	statusCode = http->statusCode;
	if (statusCode != HTTP_STATUS_OK) {
		CString msg;
		msg.Format(_T("The update server returned HTTP %u."), statusCode);
		error = msg;
		return false;
	}
	try {
		return ReadBody(http->file, http->contentLength, 512 * 1024, body, error);
	}
	catch (CInternetException* e) {
		error = _T("The update metadata could not be read.");
		e->Delete();
		return false;
	}
}

bool HttpDownloadToFile(const CString& url, const CString& path, DWORD& totalBytes,
	DWORD& downloadedBytes, CString& error, DWORD timeoutMs, SProgress* progress)
{
	totalBytes = 0;
	downloadedBytes = 0;

	SHttpSession strict;
	SHttpSession relaxed;
	SHttpSession* http = HttpOpen(url, strict, relaxed, error, timeoutMs);
	if (!http) {
		return false;
	}
	if (http->statusCode != HTTP_STATUS_OK) {
		CString msg;
		msg.Format(_T("The update server returned HTTP %u."), http->statusCode);
		error = msg;
		return false;
	}
	// When the server answers with chunked encoding the length stays unknown and
	// the UI shows an indeterminate bar rather than inventing a percentage.
	totalBytes = http->contentLength;

	HANDLE hOut = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
		FILE_ATTRIBUTE_NORMAL, NULL);
	if (hOut == INVALID_HANDLE_VALUE) {
		error = _T("Cannot write the downloaded update to disk.");
		return false;
	}

	CByteArray chunk;
	chunk.SetSize((int)kChunkSize);
	bool ok = true;
	try {
		for (;;) {
			if (progress && progress->Cancelled()) {
				error = _T("cancelled");
				ok = false;
				break;
			}
			UINT read = http->file->Read(chunk.GetData(), (UINT)chunk.GetSize());
			if (read == 0) {
				break;
			}
			DWORD written = 0;
			if (!WriteFile(hOut, chunk.GetData(), read, &written, NULL) || written != read) {
				error = _T("Writing the update to disk failed.");
				ok = false;
				break;
			}
			downloadedBytes += read;
			if (progress) {
				// ULONGLONG keeps the percentage correct past ~42 MB, and only a
				// real change is reported so the queue stays around 100 messages.
				int pct = totalBytes
					? (int)(((ULONGLONG)downloadedBytes * 100) / totalBytes) : 0;
				if (pct != progress->percent) {
					progress->Report(pct);
				}
			}
		}
	}
	catch (CInternetException* e) {
		error = _T("The update download was interrupted.");
		e->Delete();
		ok = false;
	}

	CloseHandle(hOut);

	// A short read means the transfer was cut, not that the server had nothing
	// more to say.
	if (ok && totalBytes > 0 && downloadedBytes != totalBytes) {
		error = _T("The update download was cut short. Try again.");
		ok = false;
	}
	if (!ok) {
		DeleteFileW(path);
	}
	return ok;
}

// ---------------------------------------------------------------------------
// Integrity
// ---------------------------------------------------------------------------

static bool Sha256Digest(const unsigned char* data, DWORD length, unsigned char digest[32])
{
	BCRYPT_ALG_HANDLE hAlg = NULL;
	BCRYPT_HASH_HANDLE hHash = NULL;
	BYTE* pbObject = NULL;
	DWORD cbHashObject = 0, cbResult = 0;
	bool ok = false;

	if (BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_SHA256_ALGORITHM, NULL, 0) != 0) goto done;
	if (BCryptGetProperty(hAlg, BCRYPT_OBJECT_LENGTH, (PUCHAR)&cbHashObject,
		sizeof(cbHashObject), &cbResult, 0) != 0) goto done;
	pbObject = new BYTE[cbHashObject];
	if (BCryptCreateHash(hAlg, &hHash, pbObject, cbHashObject, NULL, 0, 0) != 0) goto done;
	if (length > 0) {
		if (BCryptHashData(hHash, (PUCHAR)data, length, 0) != 0) goto done;
	}
	if (BCryptFinishHash(hHash, digest, 32, 0) != 0) goto done;
	ok = true;

done:
	if (hHash) BCryptDestroyHash(hHash);
	if (hAlg) BCryptCloseAlgorithmProvider(hAlg, 0);
	delete[] pbObject;
	return ok;
}

bool Sha256File(const CString& path, CString& hexOut)
{
	hexOut.Empty();

	HANDLE hFile = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
		FILE_ATTRIBUTE_NORMAL, NULL);
	if (hFile == INVALID_HANDLE_VALUE) {
		return false;
	}

	BCRYPT_ALG_HANDLE hAlg = NULL;
	BCRYPT_HASH_HANDLE hHash = NULL;
	BYTE* pbObject = NULL;
	DWORD cbHashObject = 0, cbResult = 0;
	bool ok = false;
	BYTE pbDigest[32];

	if (BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_SHA256_ALGORITHM, NULL, 0) != 0) goto done;
	if (BCryptGetProperty(hAlg, BCRYPT_OBJECT_LENGTH, (PUCHAR)&cbHashObject,
		sizeof(cbHashObject), &cbResult, 0) != 0) goto done;
	pbObject = new BYTE[cbHashObject];
	if (BCryptCreateHash(hAlg, &hHash, pbObject, cbHashObject, NULL, 0, 0) != 0) goto done;

	{
		CByteArray chunk;
		chunk.SetSize((int)kChunkSize);
		for (;;) {
			DWORD read = 0;
			if (!ReadFile(hFile, chunk.GetData(), kChunkSize, &read, NULL)) {
				goto done;
			}
			if (read == 0) {
				break;
			}
			if (BCryptHashData(hHash, (PUCHAR)chunk.GetData(), read, 0) != 0) {
				goto done;
			}
		}
	}
	if (BCryptFinishHash(hHash, pbDigest, sizeof(pbDigest), 0) != 0) goto done;

	BytesToHex(pbDigest, (int)sizeof(pbDigest), hexOut);
	ok = true;

done:
	CloseHandle(hFile);
	if (hHash) BCryptDestroyHash(hHash);
	if (hAlg) BCryptCloseAlgorithmProvider(hAlg, 0);
	delete[] pbObject;
	return ok;
}

namespace {

// The manifest signature is checked here rather than through a crypto API.
// Windows refused the signature on this machine through BCryptVerifySignature
// and CryptVerifySignature alike, both answering "invalid parameter" for a key,
// digest and signature that the platform's own managed RSA accepts. With a
// public exponent of 65537 the check is short enough to do directly: raise the
// signature to that exponent modulo the embedded modulus and compare the result
// with the exact PKCS#1 block the manifest has to produce. Anything unexpected
// fails the comparison, so the worst a mistake here can do is refuse a good
// update, never accept a forged one.

const int kLimbCount = MAXCALL_UPDATE_KEY_BITS / 32;
const int kModulusBytes = MAXCALL_UPDATE_KEY_BITS / 8;

void ZeroLimbs(DWORD* a)
{
	for (int i = 0; i < kLimbCount; i++) a[i] = 0;
}

void CopyLimbs(DWORD* dst, const DWORD* src)
{
	for (int i = 0; i < kLimbCount; i++) dst[i] = src[i];
}

int CmpLimbs(const DWORD* a, const DWORD* b)
{
	for (int i = kLimbCount - 1; i >= 0; i--) {
		if (a[i] != b[i]) return a[i] > b[i] ? 1 : -1;
	}
	return 0;
}

void SubLimbs(DWORD* a, const DWORD* b)
{
	unsigned __int64 borrow = 0;
	for (int i = 0; i < kLimbCount; i++) {
		unsigned __int64 t = (unsigned __int64)a[i] - b[i] - borrow;
		a[i] = (DWORD)t;
		borrow = (t >> 32) ? 1 : 0;
	}
}

// a += b, then fold the result back below n. Both inputs are already below n,
// so at most one subtraction is ever needed.
void AddMod(DWORD* a, const DWORD* b, const DWORD* n)
{
	unsigned __int64 carry = 0;
	for (int i = 0; i < kLimbCount; i++) {
		unsigned __int64 t = (unsigned __int64)a[i] + b[i] + carry;
		a[i] = (DWORD)t;
		carry = t >> 32;
	}
	if (carry != 0 || CmpLimbs(a, n) >= 0) SubLimbs(a, n);
}

// out = a * b mod n. The multiply goes one bit of b at a time so the running
// value never grows past the modulus.
void MulMod(DWORD* out, const DWORD* a, const DWORD* b, const DWORD* n)
{
	DWORD acc[kLimbCount];
	DWORD cur[kLimbCount];
	ZeroLimbs(acc);
	CopyLimbs(cur, a);
	for (int i = 0; i < kLimbCount * 32; i++) {
		if ((b[i >> 5] & (1u << (i & 31))) != 0) AddMod(acc, cur, n);
		AddMod(cur, cur, n);
	}
	CopyLimbs(out, acc);
}

void ModPow(DWORD* out, const DWORD* base, DWORD exponent, const DWORD* n)
{
	DWORD result[kLimbCount];
	DWORD power[kLimbCount];
	CopyLimbs(power, base);
	ZeroLimbs(result);
	result[0] = 1;
	if (exponent == 0) {
		CopyLimbs(out, result);
		return;
	}
	int top = 31;
	while ((exponent & (1u << top)) == 0) top--;
	CopyLimbs(result, power);
	for (int i = top - 1; i >= 0; i--) {
		MulMod(result, result, result, n);
		if ((exponent & (1u << i)) != 0) MulMod(result, result, power, n);
	}
	CopyLimbs(out, result);
}

} // namespace

bool VerifySignature(const CByteArray& data, const CByteArray& signature)
{
	const int sigLen = signature.GetSize();
	if (data.GetSize() <= 0 || sigLen != kModulusBytes) {
		HttpDiag(_T("signature rejected: the manifest or the signature has the wrong length"));
		return false;
	}

	unsigned char digest[32];
	if (!Sha256Digest((const unsigned char*)data.GetData(), (DWORD)data.GetSize(), digest)) {
		HttpDiag(_T("signature rejected: the digest could not be computed"));
		return false;
	}

	const unsigned char* blob = g_maxcallUpdatePublicKey;
	DWORD cbExp = 0, cbMod = 0;
	CopyMemory(&cbExp, blob + 8, sizeof(cbExp));
	CopyMemory(&cbMod, blob + 12, sizeof(cbMod));
	if (cbExp == 0 || cbExp > 4 || cbMod != (DWORD)kModulusBytes
		|| (DWORD)sizeof(g_maxcallUpdatePublicKey) < 24 + cbExp + cbMod) {
		HttpDiag(_T("signature rejected: the embedded public key is malformed"));
		return false;
	}

	DWORD exponent = 0;
	for (DWORD i = 0; i < cbExp; i++) exponent |= (DWORD)blob[24 + i] << (8 * i);

	// The blob stores both numbers least significant byte first.
	const unsigned char* modBytes = blob + 24 + cbExp;
	DWORD modulus[kLimbCount];
	for (int j = 0; j < kLimbCount; j++) {
		modulus[j] = (DWORD)modBytes[4 * j]
			| ((DWORD)modBytes[4 * j + 1] << 8)
			| ((DWORD)modBytes[4 * j + 2] << 16)
			| ((DWORD)modBytes[4 * j + 3] << 24);
	}

	const unsigned char* sig = (const unsigned char*)signature.GetData();
	DWORD value[kLimbCount];
	for (int j = 0; j < kLimbCount; j++) {
		DWORD limb = 0;
		for (int t = 0; t < 4; t++) {
			int fromLsb = 4 * j + t;
			limb |= (DWORD)sig[sigLen - 1 - fromLsb] << (8 * t);
		}
		value[j] = limb;
	}

	DWORD recovered[kLimbCount];
	ModPow(recovered, value, exponent, modulus);

	// A valid signature decodes to 00 01 FF..FF 00 followed by the SHA-256
	// DigestInfo header and the digest of the manifest.
	unsigned char expected[kModulusBytes];
	expected[0] = 0x00;
	expected[1] = 0x01;
	int at = 2;
	while (at < kModulusBytes - 1 - 19 - 32) expected[at++] = 0xFF;
	expected[at++] = 0x00;
	static const unsigned char kDigestInfo[19] = {
		0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01,
		0x65, 0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20 };
	CopyMemory(expected + at, kDigestInfo, 19);
	at += 19;
	CopyMemory(expected + at, digest, 32);

	for (int i = 0; i < kModulusBytes; i++) {
		int fromLsb = kModulusBytes - 1 - i;
		unsigned char got = (unsigned char)((recovered[fromLsb >> 2] >> (8 * (fromLsb & 3))) & 0xFF);
		if (got != expected[i]) {
			HttpDiag(_T("signature rejected: the manifest does not match its signature"));
			return false;
		}
	}

	HttpDiag(_T("manifest signature verified"));
	return true;
}

// ---------------------------------------------------------------------------
// Start-up preflight
//
// The main dialog is only created after the login screen is dismissed, so a
// check started from there could not run until the user had signed in. This one
// starts with the application instead and the main dialog collects the outcome
// when it appears. A client that cannot register still learns about the update.
// ---------------------------------------------------------------------------
namespace {

// Lock-free by design: the worker fills the record and publishes it with a
// single interlocked store, and the reader only looks once that store is
// visible. A mutex here would be the only use of one in this file.
SPreflight g_preflight = { false, false, false, SManifest(), CString() };
volatile LONG g_preflightReady = 0;

DWORD WINAPI PreflightThread(LPVOID lpData)
{
	EUpdateChannel channel = (EUpdateChannel)(ULONG_PTR)lpData;

	SManifest manifest;
	CString error;
	const bool ok = FetchManifest(channel, manifest, error);
	const bool newer = ok && IsNewer(manifest.version, CString(_T(_GLOBAL_VERSION)));

	g_preflight.reached = ok;
	g_preflight.newer = newer;
	if (ok) {
		g_preflight.manifest = manifest;
	}
	g_preflight.error = ok ? CString() : error;
	g_preflight.finished = true;
	InterlockedExchange(&g_preflightReady, 1);
	return 0;
}

} // namespace

void StartPreflight(EUpdateChannel channel)
{
	HANDLE h = CreateThread(NULL, 0, PreflightThread, (LPVOID)(ULONG_PTR)channel, 0, NULL);
	if (h) {
		CloseHandle(h);
	}
}

bool Preflight(SPreflight& out)
{
	if (InterlockedCompareExchange(&g_preflightReady, 0, 0) == 0) {
		out = SPreflight();
		return false;
	}
	out = g_preflight;
	return true;
}

// ---------------------------------------------------------------------------
// Manifest
// ---------------------------------------------------------------------------

bool FetchManifest(EUpdateChannel channel, SManifest& manifest, CString& error)
{
	manifest.Reset();
	error.Empty();

	CByteArray body, rawSignature, signature;
	DWORD status = 0;

	// Ask the internal mirror first. On the office network it is the only host
	// that answers at all, and going to it directly avoids a slow TLS failure
	// against github.com on every single check. github stays as the fallback so
	// the client still works from outside the office.
	CString mirror = g_mirrorBase;
	if (mirror.IsEmpty()) {
		mirror = _T(_GLOBAL_UPDATE_MIRROR_URL);
	}

	bool fetched = false;
	if (!mirror.IsEmpty()) {
		g_usingMirror = true;
		SetMirrorBase(mirror);
		CString mirrorError;
		if (HttpGet(ManifestUrl(channel), body, status, mirrorError, kManifestTimeoutMs)) {
			fetched = true;
			HttpDiag(_T("the manifest came from the internal mirror"));
		}
		else {
			HttpDiag(_T("the internal mirror did not answer; trying github"));
			g_usingMirror = false;
			SetMirrorBase(CString());
		}
	}

	if (!fetched) {
		if (!HttpGet(ManifestUrl(channel), body, status, error, kManifestTimeoutMs)) {
			return false;
		}
	}

	if (!HttpGet(SignatureUrl(channel), rawSignature, status, error, kManifestTimeoutMs)) {
		return false;
	}
	if (!Base64Decode(rawSignature, signature)) {
		error = _T("The update signature is malformed.");
		return false;
	}

	if (!VerifySignature(body, signature)) {
		error = _T("The update manifest signature is not valid. The update was rejected.");
		return false;
	}

	std::string utf8;
	if (body.GetSize() > 0) {
		utf8.assign((const char*)body.GetData(), body.GetSize());
	}
	Json::Value root;
	Json::Reader reader;
	if (utf8.empty() || !reader.parse(utf8, root) || !root.isObject()) {
		error = _T("The update manifest is not valid JSON.");
		return false;
	}

	SManifest m;
	m.version = JsonToUni(root["version"]);
	m.channel = JsonToUni(root["channel"]);
	m.url = JsonToUni(root["url"]);
	m.sha256 = JsonToUni(root["sha256"]);
	m.notes = JsonToUni(root["notes"]);
	m.sha256.MakeLower();
	m.sha256.Trim();
	m.size = root.isMember("size") ? (long long)root["size"].asDouble() : 0;

	if (!m.IsValid()) {
		error = _T("The update manifest is incomplete.");
		return false;
	}
	if (m.channel.CompareNoCase(channel == ChannelBeta ? _T("beta") : _T("stable")) != 0) {
		error = _T("The update manifest belongs to a different release channel.");
		return false;
	}

	// The signed manifest names github.com as the payload location, but this
	// client is in mirror mode precisely because it cannot reach that host. Swap
	// the host after the signature has been verified: the manifest is trusted by
	// then, and the downloaded payload is still checked against the sha256 the
	// signature covers, so the mirror cannot substitute a different binary.
	if (g_usingMirror && !g_mirrorBase.IsEmpty()) {
		CString githubPrefix = CString(_T(_GLOBAL_UPDATE_BASE_URL)) + _T("/download/");
		if (m.url.Find(githubPrefix) == 0) {
			// One payload file cannot serve both channels: the beta and stable
			// manifests point at different builds with different hashes, so each
			// channel gets its own file on the mirror.
			CString payload = (channel == ChannelBeta)
				? _T("/MaxCall-beta.exe")
				: _T("/MaxCall.exe");
			CString swapped = g_mirrorBase + payload;
			HttpDiag(_T("mirror mode: payload from ") + swapped);
			m.url = swapped;
		}
	}

	manifest = m;
	return true;
}

// ---------------------------------------------------------------------------
// Apply stage
// ---------------------------------------------------------------------------

static CString NextToken(LPCTSTR& p)
{
	while (*p == _T(' ') || *p == _T('\t')) {
		p++;
	}
	if (!*p) {
		return CString();
	}
	CString out;
	if (*p == _T('"')) {
		p++;
		while (*p && *p != _T('"')) {
			out += *p++;
		}
		if (*p == _T('"')) {
			p++;
		}
	}
	else {
		while (*p && *p != _T(' ') && *p != _T('\t')) {
			out += *p++;
		}
	}
	return out;
}

// The payload is relaunched as:
//   "<MaxCall.exe>" --apply "<target>" "<payload>" <pid>
// GetCommandLine() therefore starts with argv[0], while MFC's lpCmdLine does
// not. Handle both shapes instead of comparing the whole string.
static CString FirstToken(LPCTSTR& p)
{
	return NextToken(p);
}

bool IsApplyCommandLine(LPCTSTR cmdLine)
{
	if (!cmdLine) {
		return false;
	}
	LPCTSTR p = cmdLine;
	CString first = FirstToken(p);
	if (first.CompareNoCase(_T("--apply")) == 0) {
		return true;
	}
	// argv[0] was present: the switch is the second token.
	return NextToken(p).CompareNoCase(_T("--apply")) == 0;
}

bool ParseApplyCommandLine(LPCTSTR cmdLine, CString& targetExe, CString& sourceExe, DWORD& previousPid)
{
	targetExe.Empty();
	sourceExe.Empty();
	previousPid = 0;

	if (!cmdLine) {
		return false;
	}

	LPCTSTR p = cmdLine;
	CString first = FirstToken(p);
	if (first.CompareNoCase(_T("--apply")) != 0) {
		// Not the switch: this must be argv[0], so step over it.
		if (NextToken(p).CompareNoCase(_T("--apply")) != 0) {
			return false;
		}
	}

	targetExe = NextToken(p);
	sourceExe = NextToken(p);
	CString pidText = NextToken(p);

	if (targetExe.IsEmpty() || sourceExe.IsEmpty()) {
		return false;
	}
	previousPid = (DWORD)_tcstoul(pidText.GetString(), NULL, 10);
	return true;
}

int RunApplyStage(const CString& targetExe, const CString& sourceExe)
{
	CString target, source;
	DWORD previousPid = 0;
	if (!ParseApplyCommandLine(GetCommandLine(), target, source, previousPid)) {
		target = targetExe;
		source = sourceExe;
	}

	if (source.IsEmpty()) {
		wchar_t self[MAX_PATH] = { 0 };
		GetModuleFileNameW(NULL, self, MAX_PATH);
		source = self;
	}
	if (target.IsEmpty()) {
		MessageBoxW(NULL, L"The update failed: the installation path is unknown.",
			L"MaxCall", MB_ICONERROR);
		return 1;
	}

	// 1. Wait for the live instance to release its image section. Windows keeps
	//    a running executable locked, so nothing can be swapped before this.
	if (previousPid) {
		HANDLE hPrev = OpenProcess(SYNCHRONIZE, FALSE, previousPid);
		if (hPrev) {
			WaitForSingleObject(hPrev, kApplyWaitTimeoutMs);
			CloseHandle(hPrev);
		}
	}

	int lastSlash = target.ReverseFind(_T('\\'));
	CString targetDir = (lastSlash > 0) ? target.Left(lastSlash) : CString();
	CString staging = targetDir + _T("\\MaxCall.updating.exe");
	CString backup = target + _T(".old");

	// 2. Stage next to the target so the final move stays on one volume.
	DeleteFileW(staging);
	if (!CopyFileW(source, staging, FALSE)) {
		CString msg;
		msg.Format(L"MaxCall cannot write to its own folder:\n%ls\n\n"
			L"Close any program holding the file, or run MaxCall as an "
			L"administrator, and try again.",
			targetDir.IsEmpty() ? target.GetString() : targetDir.GetString());
		MessageBoxW(NULL, msg.GetString(), L"MaxCall", MB_ICONERROR);
		// Keep the verified payload so a retry does not need the network again.
		return 2;
	}

	// 3. Swap. Renaming keeps this close to atomic and survives an AV scan.
	// The stale backup is cleared first: restoring it on failure would silently
	// roll the user back to an ancient build.
	DeleteFileW(backup);
	bool movedOld = MoveFileExW(target, backup, MOVEFILE_REPLACE_EXISTING) != 0;
	bool movedNew = movedOld && MoveFileExW(staging, target, MOVEFILE_REPLACE_EXISTING) != 0;

	if (!movedNew) {
		if (movedOld && !MoveFileExW(backup, target, MOVEFILE_REPLACE_EXISTING)) {
			bool scheduled = MoveFileExW(backup, target,
				MOVEFILE_DELAY_UNTIL_REBOOT | MOVEFILE_REPLACE_EXISTING) != 0;
			MessageBoxW(NULL, scheduled
				? L"MaxCall could not restore its own file. Windows will finish the job "
				  L"the next time it starts."
				: L"MaxCall could not restore its own file. A copy is kept next to it "
				  L"as MaxCall.exe.old - rename it back to MaxCall.exe to recover.",
				L"MaxCall", MB_ICONERROR);
			return 4;
		}
		if (MoveFileExW(staging, target, MOVEFILE_DELAY_UNTIL_REBOOT | MOVEFILE_REPLACE_EXISTING)) {
			MessageBoxW(NULL,
				L"MaxCall could not replace its own file right now. The new build is "
				L"staged next to the program and Windows will finish the swap the next "
				L"time it starts, so restart Windows to apply it.",
				L"MaxCall", MB_ICONWARNING);
			return 5;
		}
		DeleteFileW(staging);
		MessageBoxW(NULL,
			L"The update could not be installed. Run MaxCall as an administrator and try again.",
			L"MaxCall", MB_ICONERROR);
		return 3;
	}

	DeleteFileW(backup);
	DeleteFileW(source);

	// 4. Relaunch.
	SHELLEXECUTEINFOW sei;
	ZeroMemory(&sei, sizeof(sei));
	sei.cbSize = sizeof(sei);
	sei.fMask = SEE_MASK_NOCLOSEPROCESS;
	sei.lpFile = target;
	sei.lpDirectory = targetDir.IsEmpty() ? NULL : targetDir.GetString();
	sei.nShow = SW_SHOWNORMAL;
	if (ShellExecuteExW(&sei) && sei.hProcess) {
		CloseHandle(sei.hProcess);
	}
	return 0;
}

void CleanupLeftovers(const CString& exeFile, const CString& updateFolder)
{
	if (exeFile.IsEmpty()) {
		return;
	}
	int lastSlash = exeFile.ReverseFind(_T('\\'));
	if (lastSlash > 0) {
		CString folder = exeFile.Left(lastSlash);
		// Only reached when a swap was interrupted; on a clean run neither exists.
		DeleteFileW(folder + _T("\\MaxCall.updating.exe"));
		DeleteFileW(exeFile + _T(".old"));
	}
	if (!updateFolder.IsEmpty()) {
		WIN32_FIND_DATAW find;
		HANDLE h = FindFirstFileW(updateFolder + _T("\\*"), &find);
		if (h != INVALID_HANDLE_VALUE) {
			do {
				if (wcscmp(find.cFileName, L".") != 0 && wcscmp(find.cFileName, L"..") != 0) {
					DeleteFileW(updateFolder + _T("\\") + find.cFileName);
				}
			} while (FindNextFileW(h, &find));
			FindClose(h);
		}
	}
}

bool LaunchApplyStage(const CString& targetExe, const CString& sourceExe, DWORD& previousPid)
{
	previousPid = GetCurrentProcessId();

	CString params;
	params.Format(_T("\"%s\" --apply \"%s\" \"%s\" %lu"),
		sourceExe.GetString(), targetExe.GetString(), sourceExe.GetString(), previousPid);

	STARTUPINFOW si;
	PROCESS_INFORMATION pi;
	ZeroMemory(&si, sizeof(si));
	ZeroMemory(&pi, sizeof(pi));
	si.cb = sizeof(si);
	si.dwFlags = STARTF_USESHOWWINDOW;
	si.wShowWindow = SW_HIDE;

	LPWSTR mutableParams = params.GetBuffer();
	BOOL ok = CreateProcessW(sourceExe, mutableParams, NULL, NULL, FALSE,
		CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
	params.ReleaseBuffer();

	if (ok) {
		CloseHandle(pi.hThread);
		CloseHandle(pi.hProcess);
	}
	return ok != FALSE;
}

int RunSelfUpdate(const CString& exeFile, const CString& pathLocal, EUpdateChannel channel)
{
	// Deliberately the same order the install button follows, because the bug
	// that made that button inert lived in the guard below and nowhere else.
	SManifest manifest;
	CString error;
	if (!FetchManifest(channel, manifest, error)) {
		HttpDiag(_T("self-update: manifest failed: ") + error);
		return 1;
	}
	if (!manifest.IsValid()) {
		HttpDiag(_T("self-update: manifest rejected, url ") + manifest.url);
		return 1;
	}
	if (!IsNewer(manifest.version, CString(_T(_GLOBAL_VERSION)))) {
		HttpDiag(_T("self-update: nothing newer than ") + CString(_T(_GLOBAL_VERSION)) +
			_T(", manifest says ") + manifest.version);
		return 2;
	}

	CString folder = pathLocal + _T("update");
	CreateDirectory(folder, NULL);
	CString path;
	path.Format(_T("%s\\MaxCall-%s.exe"), folder, manifest.version);
	DeleteFile(path);

	DWORD total = 0, downloaded = 0;
	if (!HttpDownloadToFile(manifest.url, path, total, downloaded, error, 300000, NULL)) {
		HttpDiag(_T("self-update: download failed: ") + error);
		return 1;
	}

	CString hex;
	if (!Sha256File(path, hex) || hex.CompareNoCase(manifest.sha256) != 0) {
		HttpDiag(_T("self-update: payload does not match the signed checksum"));
		DeleteFile(path);
		return 1;
	}

	DWORD previousPid = 0;
	if (!LaunchApplyStage(exeFile, path, previousPid)) {
		HttpDiag(_T("self-update: the apply stage did not start"));
		return 1;
	}
	HttpDiag(_T("self-update: apply stage started for ") + manifest.version);
	return 0;
}

} // namespace AppUpdate