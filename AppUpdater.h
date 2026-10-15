#pragma once

#include <afxwin.h>

// ---------------------------------------------------------------------------
// AppUpdater - self update engine for MaxCall.
//
// The release pipeline signs one small manifest (update.json) with an RSA key
// that never exists in this repository. The client verifies that signature
// locally against UpdatePublicKey.h, and only then downloads and runs the new
// executable. Two channels are supported so internal builds can ship to the
// developer without reaching end users:
//
//   stable -> releases/latest/download/update.json   (published from a v* tag)
//   beta   -> releases/download/beta/update.json     (published on every push to main)
//
// The module is UI-free apart from two unavoidable message boxes in the apply
// stage. Progress travels to the window through SUpdateEvent, which CmainDlg
// marshals onto the UI thread.
// ---------------------------------------------------------------------------

namespace AppUpdate
{

enum EUpdateChannel
{
	ChannelStable = 0,
	ChannelBeta = 1,
};

// Reported to the UI. Keep in sync with web-shell/app.js onUpdateState().
enum EUpdateState
{
	StateNone = 0,      // nothing pending
	StateChecking = 1,  // manifest request in flight
	StateAvailable = 2, // newer version found, waiting for user confirmation
	StateDownloading = 3,
	StateVerifying = 4,
	StateReady = 5,     // payload verified, applying
	StateApplied = 6,   // process is handing over to the new build
	StateError = 7,
};

// Payload handed to the window thread through PostMessage. Carries the whole
// manifest because the UI decides later whether to install it, long after the
// worker thread that fetched it is gone.
struct SUpdateEvent
{
	int state;
	int percent;
	bool manual;
	CString version;
	CString channel;
	CString url;
	CString sha256;
	CString message;

	SUpdateEvent() : state(StateNone), percent(0), manual(false) {}
	SUpdateEvent(int s, const CString& v, int p, const CString& m, bool byUser = false)
		: state(s), percent(p), manual(byUser), version(v), message(m) {}
	void AttachManifest(const struct SManifest& m);
};

// Signed description of a published build.
struct SManifest
{
	CString version;
	CString channel;
	CString url;
	CString sha256;
	CString notes;
	long long size;

	SManifest() : size(0) {}
	void Reset();
	bool IsValid() const;
};

// Cooperative cancellation plus optional progress reporting back to the window.
// Report() allocates one event per percentage step, so callers must only invoke
// it when the percentage actually changed.
struct SProgress
{
	volatile LONG percent;
	volatile LONG cancel;
	HWND hNotify;
	UINT notifyMessage;
	CString version;

	SProgress() : percent(0), cancel(0), hNotify(NULL), notifyMessage(0) {}

	void Cancel() { InterlockedExchange(&cancel, 1); }
	bool Cancelled() const { return InterlockedCompareExchange((volatile LONG*)&cancel, 0, 0) != 0; }

	void Report(int p)
	{
		InterlockedExchange(&percent, p);
		if (!hNotify) {
			return;
		}
		SUpdateEvent* ev = new SUpdateEvent(StateDownloading, version, p, CString());
		if (!PostMessage(hNotify, notifyMessage, 0, (LPARAM)ev)) {
			delete ev;
		}
	}
};

// --- versioning ------------------------------------------------------------

// Semver-ish compare. Understands "6.1.5", "6.1.6-beta.42" and "6.1.6+build".
// Returns <0 when a is older, 0 when equal, >0 when a is newer.
int CompareVersion(const CString& a, const CString& b);
bool IsNewer(const CString& candidate, const CString& current);
CString VersionCore(const CString& version);

// --- channel ---------------------------------------------------------------

CString ManifestUrl(EUpdateChannel channel);
CString SignatureUrl(EUpdateChannel channel);

// Mirrors. Sites that cannot reach github.com from their network can serve the
// same signed manifests themselves; the manifest stays verifiable either way
// because the signature is checked against the embedded key, not the host.
void SetMirrorBase(const CString& base);
CString MirrorBase();

// --- transport -------------------------------------------------------------

// TLS with full certificate validation. Unlike URLGetAsync, this never disables
// certificate checks - the payload is an executable, so a hijacked connection
// would otherwise be equivalent to running arbitrary code.
bool HttpGet(const CString& url, CByteArray& body, DWORD& statusCode, CString& error, DWORD timeoutMs);
bool HttpDownloadToFile(const CString& url, const CString& path, DWORD& totalBytes,
	DWORD& downloadedBytes, CString& error, DWORD timeoutMs, SProgress* progress);

// --- integrity -------------------------------------------------------------

bool Sha256File(const CString& path, CString& hexOut);
bool VerifySignature(const CByteArray& data, const CByteArray& signature);

// --- manifest --------------------------------------------------------------

// Fetches, verifies and parses the signed manifest for the given channel.
bool FetchManifest(EUpdateChannel channel, SManifest& manifest, CString& error);

// Check that runs during start-up, before the login screen exists, so a client
// learns about a newer build without first having to register. The result is
// kept here and picked up by the main dialog once it is created.
struct SPreflight
{
	bool finished;
	bool reached;
	bool newer;
	SManifest manifest;
	CString error;
};

void StartPreflight(EUpdateChannel channel);
bool Preflight(SPreflight& out);

// --- apply stage -----------------------------------------------------------

// True when this process was started as the update payload (MaxCall.exe --apply).
// Accepts both the raw command line (with argv[0]) and MFC's lpCmdLine.
bool IsApplyCommandLine(LPCTSTR cmdLine);
bool ParseApplyCommandLine(LPCTSTR cmdLine, CString& targetExe, CString& sourceExe, DWORD& previousPid);

// Blocks until the running instance exits, swaps the new binary in place and
// relaunches. Runs without a UI dependency so it can finish even if the main
// window never got the chance to shut down cleanly.
int RunApplyStage(const CString& targetExe, const CString& sourceExe);

// Hands control over to a freshly downloaded build: starts it with --apply and
// reports the PID that instance must wait for.
bool LaunchApplyStage(const CString& targetExe, const CString& sourceExe, DWORD& previousPid);

// Removes the scratch files an update leaves behind, so the program's folder
// only ever contains MaxCall.exe again. Safe to call on every start: it never
// touches MaxCall.exe itself and ignores anything that vanished meanwhile.
void CleanupLeftovers(const CString& exeFile, const CString& updateFolder);

} // namespace AppUpdate