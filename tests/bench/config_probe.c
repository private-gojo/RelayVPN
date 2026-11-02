#include <stdio.h>

#include <lwipopts.h>
#include <tun2socks/tun2socks.h>

#ifndef TCP_WND
#define TCP_WND (4 * TCP_MSS)
#endif

#ifndef MEMP_NUM_TCP_PCB
#define MEMP_NUM_TCP_PCB 5
#endif

#ifndef MEMP_MEM_MALLOC
#define MEMP_MEM_MALLOC 0
#endif

int main(void)
{
    printf("%d,%d,%d,%d,%d\n",
        TCP_WND,
        TCP_SND_BUF,
        CLIENT_SOCKS_RECV_BUF_SIZE,
        MEMP_NUM_TCP_PCB,
        MEMP_MEM_MALLOC);
    return 0;
}
