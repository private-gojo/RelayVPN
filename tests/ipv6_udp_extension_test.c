#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <misc/ipv6_proto.h>

static int expect_udp(
    const struct ipv6_header *header,
    uint8_t *payload,
    int payload_len,
    int expected_offset
)
{
    uint8_t *udp_data = NULL;
    int udp_data_len = -1;

    if (!ipv6_udp_payload(header, payload, payload_len, &udp_data, &udp_data_len)) {
        return 0;
    }

    return udp_data == payload + expected_offset &&
           udp_data_len == payload_len - expected_offset;
}

int main(void)
{
    struct ipv6_header header;
    memset(&header, 0, sizeof(header));

    uint8_t payload[64] = {0};
    uint8_t *udp_data = NULL;
    int udp_data_len = -1;

    header.next_header = IPV6_NEXT_UDP;
    if (!expect_udp(&header, payload, 16, 0)) {
        fprintf(stderr, "Direct UDP header was not recognized\n");
        return 1;
    }

    header.next_header = IPV6_NEXT_HOP_BY_HOP;
    payload[0] = IPV6_NEXT_UDP;
    payload[1] = 0;
    if (!expect_udp(&header, payload, 16, 8)) {
        fprintf(stderr, "Hop-by-Hop extension was not skipped\n");
        return 1;
    }

    header.next_header = IPV6_NEXT_ROUTING;
    payload[0] = IPV6_NEXT_DESTINATION;
    payload[1] = 0;
    payload[8] = IPV6_NEXT_UDP;
    payload[9] = 0;
    if (!expect_udp(&header, payload, 24, 16)) {
        fprintf(stderr, "Routing and Destination extensions were not skipped\n");
        return 1;
    }

    header.next_header = IPV6_NEXT_AH;
    payload[0] = IPV6_NEXT_UDP;
    payload[1] = 1;
    if (!expect_udp(&header, payload, 16, 12)) {
        fprintf(stderr, "AH extension length was not handled\n");
        return 1;
    }

    header.next_header = IPV6_NEXT_FRAGMENT;
    payload[0] = IPV6_NEXT_UDP;
    payload[1] = 0;
    payload[2] = 0;
    payload[3] = 0;
    payload[4] = payload[5] = payload[6] = payload[7] = 0;
    if (!expect_udp(&header, payload, 16, 8)) {
        fprintf(stderr, "Atomic Fragment header was not skipped\n");
        return 1;
    }

    payload[3] = 8;
    if (ipv6_udp_payload(&header, payload, 16, &udp_data, &udp_data_len)) {
        fprintf(stderr, "Non-initial IPv6 fragment was accepted\n");
        return 1;
    }

    payload[3] = 0;
    if (ipv6_udp_payload(&header, payload, 7, &udp_data, &udp_data_len)) {
        fprintf(stderr, "Truncated IPv6 extension was accepted\n");
        return 1;
    }

    header.next_header = 50;
    if (ipv6_udp_payload(&header, payload, 16, &udp_data, &udp_data_len)) {
        fprintf(stderr, "Opaque ESP payload was accepted as UDP\n");
        return 1;
    }

    puts("PASS: IPv6 UDP extension parsing");
    return 0;
}
