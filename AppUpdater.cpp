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
	// Anything other than HTTPS would defeat the signature check in practice.
	return url.Left(8).CompareNoCase(_T("https://")) == 0;
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

CString ManifestUrl(EUpdateChannel channel)
{
	return (channel == ChannelBeta)
		? CString(_T(_GLOBAL_UPDATE_BETA_URL))
		: CString(_T(_GLOBAL_UPDATE_STABLE_URL));
}

CString SignatureUrl(EUpdateChannel channel)
{
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

// Transport uses MFC's CInternetSession wrapper, the same API global.cpp already
// relies on for URLGetAsync. Certificate validation is left at its default (on):
// unlike that helper we never disable any of the SECURITY_FLAG_IGNORE_* checks,
// because this channel ends in running a downloaded executable.
struct SHttpSession
{
	CInternetSession session;
	CHttpConnection* connection;
	CHttpFile* file;

	SHttpSession() : connection(NULL), file(NULL) {}
	~SHttpSession() { Close(); }

	void Close()
	{
		if (file) { file->Close(); delete file; file = NULL; }
		if (connection) { connection->Close(); delete connection; connection = NULL; }
	}
};

// Splits an absolute URL into the parts the transport needs. Deliberately hand
// rolled: MFC offers AfxParseURL and AfxParseURLEx with different parameter
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
	if (scheme != _T("https")) {
		return false;
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

static bool HttpOpen(const CString& url, SHttpSession& http, DWORD& statusCode,
	CString& error, DWORD timeoutMs)
{
	statusCode = 0;

	CString scheme, host, object;
	INTERNET_PORT port = INTERNET_DEFAULT_HTTPS_PORT;
	if (!SplitUrl(url, scheme, host, port, object)) {
		error = _T("The update URL is malformed or is not HTTPS.");
		return false;
	}
	// The manifest is signed, so this is defence in depth rather than the primary
	// control: no executable may be fetched from anywhere but the release host.
	if (!IsAllowedUpdateHost(host)) {
		error = _T("The update is hosted on an unexpected server and was refused.");
		return false;
	}

	DWORD timeout = timeoutMs / 1000;
	if (timeout == 0) {
		timeout = 1;
	}
	http.session.SetOption(INTERNET_OPTION_CONNECT_TIMEOUT, &timeout, sizeof(timeout));

	http.connection = http.session.GetHttpConnection(host, INTERNET_FLAG_SECURE, port);
	if (!http.connection) {
		error = _T("Cannot reach the update server.");
		return false;
	}
	http.connection->SetOption(INTERNET_OPTION_RECEIVE_TIMEOUT, &timeout, sizeof(timeout));
	http.connection->SetOption(INTERNET_OPTION_SEND_TIMEOUT, &timeout, sizeof(timeout));

	http.file = http.connection->OpenRequest(_T("GET"), object, NULL, NULL, NULL,
		INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE | INTERNET_FLAG_KEEP_CONNECTION, 0);
	if (!http.file) {
		error = _T("Cannot create the update request.");
		return false;
	}

	statusCode = http.file->GetStatusCode();
	return true;
}

// Reads one response body. CHttpFile reports end of stream as a zero-length read
// and does not distinguish it from a failure, so a short body is caught afterwards
// by comparing against Content-Length. maxBytes stops a hostile or broken server
// from making the client allocate without bound.
static bool ReadBody(CHttpFile* file, CByteArray& out, DWORD expected, DWORD maxBytes,
	CString& error)
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

	SHttpSession http;
	if (!HttpOpen(url, http, statusCode, error, timeoutMs)) {
		return false;
	}
	if (statusCode != HTTP_STATUS_OK) {
		CString msg;
		msg.Format(_T("The update server returned HTTP %u."), statusCode);
		error = msg;
		return false;
	}
	return ReadBody(http.file, body, http.file->GetLength(), 512 * 1024, error);
}

bool HttpDownloadToFile(const CString& url, const CString& path, DWORD& totalBytes,
	DWORD& downloadedBytes, CString& error, DWORD timeoutMs, SProgress* progress)
{
	totalBytes = 0;
	downloadedBytes = 0;

	SHttpSession http;
	DWORD statusCode = 0;
	if (!HttpOpen(url, http, statusCode, error, timeoutMs)) {
		return false;
	}
	if (statusCode != HTTP_STATUS_OK) {
		CString msg;
		msg.Format(_T("The update server returned HTTP %u."), statusCode);
		error = msg;
		return false;
	}
	// CHttpFile records Content-Length while parsing the headers. When the server
	// answers with chunked encoding the total is unknown and the UI simply shows an
	// indeterminate bar rather than inventing a percentage.
	totalBytes = http.file->GetLength();

	HANDLE hOut = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
		FILE_ATTRIBUTE_NORMAL, NULL);
	if (hOut == INVALID_HANDLE_VALUE) {
		error = _T("Cannot write the downloaded update to disk.");
		return false;
	}

	CByteArray chunk;
	chunk.SetSize((int)kChunkSize);
	bool ok = true;
	for (;;) {
		if (progress && progress->Cancelled()) {
			error = _T("cancelled");
			ok = false;
			break;
		}
		UINT read = http.file->Read(chunk.GetData(), (UINT)chunk.GetSize());
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
			// ULONGLONG keeps the percentage correct past ~42 MB, and only a real
			// change is reported so the message queue stays at ~100 entries.
			int pct = totalBytes
				? (int)(((ULONGLONG)downloadedBytes * 100) / totalBytes) : 0;
			if (pct != progress->percent) {
				progress->Report(pct);
			}
		}
	}

	CloseHandle(hOut);

	// A short read means the transfer was cut, not that the server had nothing
	// more to say; the caller's checksum would catch it but the message is clearer.
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

bool VerifySignature(const CByteArray& data, const CByteArray& signature)
{
	if (data.GetSize() <= 0
		|| signature.GetSize() != (int)(MAXCALL_UPDATE_KEY_BITS / 8)) {
		return false;
	}

	unsigned char digest[32];
	if (!Sha256Digest((const unsigned char*)data.GetData(), (DWORD)data.GetSize(), digest)) {
		return false;
	}

	BCRYPT_ALG_HANDLE hAlg = NULL;
	BCRYPT_KEY_HANDLE hKey = NULL;
	bool ok = false;

	if (BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_RSA_ALGORITHM, NULL, 0) != 0) goto done;
	if (BCryptImportKeyPair(hAlg, NULL, BCRYPT_RSAPUBLIC_BLOB, &hKey,
		(const PUCHAR)g_maxcallUpdatePublicKey, (ULONG)sizeof(g_maxcallUpdatePublicKey), 0) != 0) {
		goto done;
	}
	{
		BCRYPT_PKCS1_PADDING_INFO padding;
		padding.pszAlgId = BCRYPT_SHA256_ALGORITHM;
		// BCryptVerifySignature returns STATUS_SUCCESS (0) when the signature is
		// good. Anything non-zero means the payload was tampered with.
		// Argument order is (hKey, pPaddingInfo, pbHashValue, cbHashValue,
		// pbSignature, cbSignature, dwFlags) - the padding info is second and
		// dwFlags comes last.
		if (BCryptVerifySignature(hKey, &padding, digest, sizeof(digest),
			(PUCHAR)signature.GetData(), (ULONG)signature.GetSize(), 0) != 0) {
			goto done;
		}
	}
	ok = true;

done:
	if (hKey) BCryptDestroyKey(hKey);
	if (hAlg) BCryptCloseAlgorithmProvider(hAlg, 0);
	return ok;
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

	if (!HttpGet(ManifestUrl(channel), body, status, error, kManifestTimeoutMs)) {
		return false;
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

} // namespace AppUpdate