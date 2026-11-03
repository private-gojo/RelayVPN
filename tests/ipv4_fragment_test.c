#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <misc/ipv4_proto.h>

int main(void)
{
    struct ipv4_header header;
    memset(&header, 0, sizeof(header));

    header.flags3_fragmentoffset13 = hton16(0);
    if (ipv4_is_fragmented(&header)) {
        fprintf(stderr, "Unfragmented IPv4 packet was marked fragmented\n");
        return 1;
    }

    header.flags3_fragmentoffset13 = hton16(0x4000);
    if (ipv4_is_fragmented(&header)) {
        fprintf(stderr, "IPv4 DF-only packet was marked fragmented\n");
        return 1;
    }

    header.flags3_fragmentoffset13 = hton16(IPV4_FLAG_MORE_FRAGMENTS);
    if (!ipv4_is_fragmented(&header)) {
        fprintf(stderr, "IPv4 MF packet was not marked fragmented\n");
        return 1;
    }

    header.flags3_fragmentoffset13 = hton16(1);
    if (!ipv4_is_fragmented(&header)) {
        fprintf(stderr, "IPv4 non-initial fragment was not marked fragmented\n");
        return 1;
    }

    puts("PASS: IPv4 fragmentation detection");
    return 0;
}
