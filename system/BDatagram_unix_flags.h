#ifndef BADVPN_SYSTEM_BDATAGRAM_UNIX_FLAGS_H
#define BADVPN_SYSTEM_BDATAGRAM_UNIX_FLAGS_H

#include <sys/socket.h>

static inline int bdatagram_unix_recv_message_truncated (int msg_flags)
{
    return (msg_flags & (MSG_TRUNC | MSG_CTRUNC)) != 0;
}

#endif
