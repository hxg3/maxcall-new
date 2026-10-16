// Ephemeral diagnostics for the atl-probe branch: certifies the ATL
// Base64Encode contract used by CrmPopupDlg::MakeBasicAuthHeader.
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <windows.h>
#include <atlenc.h>

static void probe_flag(DWORD flags, const char* name, const char* src, int srcLen, int upper) {
	char buf[512];
	printf("flags=%s (srcLen=%d):\n", name, srcLen);
	for (int dl = 1; dl <= upper; dl++) {
		int destLen = dl;
		memset(buf, 0, sizeof(buf));
		int written = Base64Encode((const BYTE*)src, srcLen, buf, &destLen, flags);
		printf("  dl=%2d ret=%d wrote=%d\n", dl, ret, destLen);
		(void)written;
	}
}

int main() {
	const char* src = "103:Abdullah@x";
	int srcLen = (int)strlen(src);
	probe_flag(ATL_BASE64_FLAG_NONE, "NONE", src, srcLen, 26);
	probe_flag(ATL_BASE64_FLAG_NOCRLF, "NOCRLF", src, srcLen, 26);

	// long input: 200 bytes (over the 64-char break threshold)
	char big[201];
	memset(big, 'x', 200);
	big[200] = 0;
	probe_flag(ATL_BASE64_FLAG_NONE, "NONE-big", big, 200, 300);
	probe_flag(ATL_BASE64_FLAG_NOCRLF, "NOCRLF-big", big, 200, 300);
	return 0;
}
