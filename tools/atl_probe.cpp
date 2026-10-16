// Ephemeral diagnostics for the atl-probe branch: certifies the ATL
// Base64Encode contract used by CrmPopupDlg::MakeBasicAuthHeader.
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <windows.h>
#include <atlenc.h>

static void probe_flag(DWORD flags, const char* name, const char* src, int srcLen) {
	char buf[256];
	printf("flags=%s:\n", name);
	for (int dl = 1; dl <= 40; dl++) {
		int destLen = dl;
		memset(buf, 0, sizeof(buf));
		int ret = Base64Encode((const BYTE*)src, srcLen, buf, &destLen, flags);
		if (ret > 0) {
			printf("  first success at destLen=%d wrote=%d out=[%s] raw=", dl, destLen, buf);
			for (int i = 0; i < destLen && i < 40; i++) {
				unsigned char c = buf[i];
				if (c < 32 || c > 126) printf("<%02X>", c); else putchar(c);
			}
			printf("\n");
			return;
		}
	}
	printf("  no success up to 40\n");
}

int main() {
	const char* src = "103:Abdullah@x";
	int srcLen = (int)strlen(src);
	probe_flag(ATL_BASE64_FLAG_NONE, "NONE", src, srcLen);
	probe_flag(ATL_BASE64_FLAG_NOCRLF, "NOCRLF", src, srcLen);
	return 0;
}
