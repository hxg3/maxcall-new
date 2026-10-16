// Ephemeral diagnostics for the atl-probe branch: certifies the ATL
// Base64Encode contract used by CrmPopupDlg::MakeBasicAuthHeader
// by mirroring the exact fixed code path.
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <windows.h>
#include <atlenc.h>

static int fails = 0;

// mirrors the fixed login in CrmPopupDlg.cpp exactly
static bool make_header(const char* username, const char* password, char* out) {
	char raw[512];
	sprintf_s(raw, sizeof(raw), "%s:%s", username, password);
	int srcLen = (int)strlen(raw);
	if (srcLen <= 0) return false;
	int destLen = ((srcLen + 2) / 3) * 4 + 2;
	char* buf = (char*)malloc(destLen);
	if (!buf) return false;
	int written = Base64Encode((const BYTE*)raw, srcLen, buf, &destLen, ATL_BASE64_FLAG_NOCRLF);
	if (written <= 0) { free(buf); return false; }
	// out = "Basic " + encoded
	sprintf_s(out, 512, "Basic %s", buf);
	free(buf);
	return true;
}

static void check(const char* user, const char* pass, const char* expectedPending) {
	char out[512];
	bool ok = make_header(user, pass, out);
	printf("user=%s pass=%s -> %s\n", user, pass, ok ? out : "FAILED");
	if (expectedPending) {
		if (!ok || strncmp(out, expectedPending, strlen(expectedPending) + 1) != 0) {
			printf("::error::NOT matching reference [%s]\n", expectedPending);
			fails++;
		} else {
			printf("   reference match OK\n");
		}
	} else if (!ok) {
		printf("::error::encoding returned zero\n");
		fails++;
	}
}

int main() {
	// short credentials - the standard case
	check("103", "Abdullah@x", "Basic MTAzOkFiZHVsYWlAeA==");
	// long credentials (100 chars each) - beyond the CRLF break threshold in NONE mode
	char u[101], p[101];
	memset(u, 'a', 100); u[100] = 0;
	memset(p, 'z', 100); p[100] = 0;
	check(u, p, "Basic YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYTp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6enp6");
	// credentials with special characters
	check("ab.c", "p@ss!w:rd/#", "Basic YWIuYzpwQHNzIXc6cmQvIw==");
	return fails;
}
