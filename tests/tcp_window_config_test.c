#include <stdio.h>

#include <lwipopts.h>
#include <tun2socks/tun2socks.h>

#if !LWIP_WND_SCALE
#error "TCP window scaling must be enabled"
#endif

#if TCP_RCV_SCALE != 2
#error "Unexpected TCP receive-window scale"
#endif

#if TCP_WND != 131072
#error "Unexpected TCP receive window"
#endif

#if TCP_SND_BUF != 131072
#error "Unexpected TCP send buffer"
#endif

#if CLIENT_SOCKS_RECV_BUF_SIZE != TCP_SND_BUF
#error "SOCKS-to-TCP ring capacity must match TCP_SND_BUF"
#endif

#if TCP_SND_QUEUELEN < ((2 * TCP_SND_BUF + TCP_MSS - 1) / TCP_MSS)
#error "TCP_SND_QUEUELEN is below the lwIP minimum"
#endif

int main(void)
{
    printf(
        "TCP_WND=%d TCP_SND_BUF=%d TCP_RCV_SCALE=%d CLIENT_SOCKS_RECV_BUF_SIZE=%d\n",
        TCP_WND,
        TCP_SND_BUF,
        TCP_RCV_SCALE,
        CLIENT_SOCKS_RECV_BUF_SIZE
    );

    return 0;
}
