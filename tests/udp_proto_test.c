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

    puts("PASS: IPv4 UDP zero, valid, and invalid checksum cases");
    return 0;
}
