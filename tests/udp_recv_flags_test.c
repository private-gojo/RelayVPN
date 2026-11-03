#include <stdio.h>
#include <sys/socket.h>

#include <system/BDatagram.h>

int main(void)
{
    if (bdatagram_unix_recv_message_truncated(0)) {
        fprintf(stderr, "Complete UDP message was marked truncated\n");
        return 1;
    }

    if (!bdatagram_unix_recv_message_truncated(MSG_TRUNC)) {
        fprintf(stderr, "Truncated UDP payload was not detected\n");
        return 1;
    }

    if (!bdatagram_unix_recv_message_truncated(MSG_CTRUNC)) {
        fprintf(stderr, "Truncated UDP control data was not detected\n");
        return 1;
    }

    if (!bdatagram_unix_recv_message_truncated(MSG_TRUNC | MSG_CTRUNC)) {
        fprintf(stderr, "Combined UDP truncation flags were not detected\n");
        return 1;
    }

    puts("PASS: UDP recvmsg truncation flag detection");
    return 0;
}
