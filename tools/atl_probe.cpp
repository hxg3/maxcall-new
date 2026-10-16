// Ephemeral diagnostics for the atl-probe branch: certifies the ATL
// Base64Encode contract used by CrmPopupDlg::MakeBasicAuthHeader.
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <windows.h>
#include <atlenc.h>

int main() {
    const char* src = "103:Abdullah@x";
    int srcLen = (int)strlen(src);

    int probeLen = 0;
    int probe = Base64Encode((const BYTE*)src, srcLen, NULL, &probeLen, ATL_BASE64_FLAG_NONE);
    printf("1) ATL probe with NULL dest: ret=%d destLen=%d -> %s\n",
           probe, probeLen, (probe != 0) ? "SUPPORTED" : "REJECTED (bug confirmed)");
    fflush(stdout);

    char* buf = (char*)malloc(((srcLen + 2) / 3) * 4 + 1);
    int chain = ((srcLen + 2) / 3) * 4 + 1;
    int ret = Base64Encode((const BYTE*)src, srcLen, buf, &chain, ATL_BASE64_FLAG_NONE);
    printf("2) ATL pre-sized: ret=%d out=[%.*s] outLen=%d\n", ret, chain, buf, chain);
    fflush(stdout);

    const char* ref = "MTAzOkFiZHVsbGFoQHg=";
    bool match = (ret > 0 && strncmp(buf, ref, strlen(ref) + 1) == 0);
    free(buf);
    if (!match) {
        printf("::error::fixed pattern did not produce the reference Basic value\n");
        return 2;
    }
    printf("3) reference match OK - the fixed code path yields a valid Basic header value\n");
    return 0;
}
