#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>

#include <misc/udp_proto.h>

int main(void)
{
    static const uint8_t payload[] = {0x52, 0x65, 0x6c, 0x61, 0x79};
    const uint32_t source_addr = htonl(0xC0000201U);
    const uint32_t dest_addr = htonl(0xC6336402U);

    struct udp_header header = {0};
    header.source_port = htons(12345);
    header.dest_port = htons(53);
    header.length = htons((uint16_t)(sizeof(header) + sizeof(payload)));

    if (!udp_ipv4_checksum_valid(
        &header, payload, (uint16_t)sizeof(payload), source_addr, dest_addr
    )) {
        fprintf(stderr, "IPv4 UDP zero checksum was rejected\n");
        return 1;
    }

    header.checksum = udp_checksum(
        &header, payload, (uint16_t)sizeof(payload), source_addr, dest_addr
    );
    if (header.checksum == 0 || !udp_ipv4_checksum_valid(
        &header, payload, (uint16_t)sizeof(payload), source_addr, dest_addr
    )) {
        fprintf(stderr, "Valid IPv4 UDP checksum was rejected\n");
        return 1;
    }

    header.checksum ^= 1;
    if (udp_ipv4_checksum_valid(
        &header, payload, (uint16_t)sizeof(payload), source_addr, dest_addr
    )) {
        fprintf(stderr, "Invalid IPv4 UDP checksum was accepted\n");
        return 1;
    }

    static const uint8_t ipv6_source[16] = {
        0x20, 0x01, 0x0d, 0xb8, 0x00, 0x01, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01
    };
    static const uint8_t ipv6_dest[16] = {
        0x20, 0x01, 0x0d, 0xb8, 0x00, 0x02, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02
    };

    header.checksum = 0;
    uint16_t ipv6_checksum = udp_ip6_checksum(
        &header,
        payload,
        (uint16_t)sizeof(payload),
        ipv6_source,
        ipv6_dest
    );
    if (ipv6_checksum == 0) {
        fprintf(stderr, "IPv6 UDP checksum generation produced an invalid value\\n");
        return 1;
    }

    header.checksum = ipv6_checksum;
    struct udp_header ipv6_verify_header = header;
    ipv6_verify_header.checksum = 0;
    if (udp_ip6_checksum(
        &ipv6_verify_header,
        payload,
        (uint16_t)sizeof(payload),
        ipv6_source,
        ipv6_dest
    ) != ipv6_checksum) {
        fprintf(stderr, "IPv6 UDP checksum was not stable for odd-length payload\\n");
        return 1;
    }

    puts("PASS: IPv4 UDP and IPv6 UDP checksum cases");
    return 0;
}
