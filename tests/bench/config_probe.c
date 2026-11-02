#include <stdio.h>
#include <lwip/opt.h>
#include <tun2socks/tun2socks.h>

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
