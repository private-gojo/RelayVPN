#include <stdio.h>
#include <string.h>

#include "../tun2socks/tcp_ring_buffer.h"

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        return 1; \
    } \
} while (0)

static int write_data(
    TcpRingBuffer *buffer,
    const uint8_t *data,
    size_t length
)
{
    TcpRingBufferRegion regions[2];
    size_t remaining = length;
    size_t offset = 0;
    int count = TcpRingBuffer_GetWriteRegions(buffer, regions);
    int i;

    if (count < 0) {
        return -1;
    }

    for (i = 0; i < count && remaining > 0; i++) {
        size_t amount = regions[i].len < remaining
            ? regions[i].len
            : remaining;

        memcpy(regions[i].data, data + offset, amount);
        offset += amount;
        remaining -= amount;
    }

    if (remaining != 0) {
        return -1;
    }

    return TcpRingBuffer_WriteCommit(buffer, length);
}

static int check_read_data(
    TcpRingBuffer *buffer,
    const uint8_t *expected,
    size_t length
)
{
    TcpRingBufferRegion regions[2];
    size_t remaining = length;
    size_t offset = 0;
    int count = TcpRingBuffer_GetReadRegions(buffer, regions);
    int i;

    if (count < 0) {
        return -1;
    }

    for (i = 0; i < count && remaining > 0; i++) {
        size_t amount = regions[i].len < remaining
            ? regions[i].len
            : remaining;

        if (memcmp(regions[i].data, expected + offset, amount) != 0) {
            return -1;
        }

        offset += amount;
        remaining -= amount;
    }

    return remaining == 0 ? 0 : -1;
}

int main(void)
{
    uint8_t storage[8];
    TcpRingBuffer buffer;
    TcpRingBufferRegion regions[2];
    static const uint8_t first[] = "abcdef";
    static const uint8_t wrapped[] = "ghijk";
    static const uint8_t expected[] = "efghijk";
    static const uint8_t final_byte[] = "Z";

    CHECK(TcpRingBuffer_Init(NULL, storage, sizeof(storage)) == -1);
    CHECK(TcpRingBuffer_Init(&buffer, NULL, sizeof(storage)) == -1);
    CHECK(TcpRingBuffer_Init(&buffer, storage, 0) == -1);
    CHECK(TcpRingBuffer_Init(&buffer, storage, sizeof(storage)) == 0);

    CHECK(TcpRingBuffer_GetCapacity(&buffer) == sizeof(storage));
    CHECK(TcpRingBuffer_GetUsed(&buffer) == 0);
    CHECK(TcpRingBuffer_GetWritable(&buffer) == sizeof(storage));
    CHECK(TcpRingBuffer_GetReadable(&buffer) == 0);
    CHECK(TcpRingBuffer_GetQueued(&buffer) == 0);
    CHECK(TcpRingBuffer_GetReadRegions(&buffer, regions) == 0);
    CHECK(TcpRingBuffer_ReadCommit(&buffer, 1) == -1);
    CHECK(TcpRingBuffer_ReadRelease(&buffer, 1) == -1);

    CHECK(write_data(&buffer, first, sizeof(first) - 1) == 0);
    CHECK(TcpRingBuffer_GetUsed(&buffer) == 6);
    CHECK(TcpRingBuffer_GetWritable(&buffer) == 2);
    CHECK(check_read_data(&buffer, first, sizeof(first) - 1) == 0);

    CHECK(TcpRingBuffer_ReadCommit(&buffer, 4) == 0);
    CHECK(TcpRingBuffer_GetQueued(&buffer) == 4);
    CHECK(TcpRingBuffer_GetReadable(&buffer) == 2);

    CHECK(TcpRingBuffer_ReadRelease(&buffer, 3) == 0);
    CHECK(TcpRingBuffer_GetUsed(&buffer) == 3);
    CHECK(TcpRingBuffer_GetQueued(&buffer) == 1);

    CHECK(TcpRingBuffer_GetWriteRegions(&buffer, regions) == 2);
    CHECK(regions[0].len == 2);
    CHECK(regions[1].len == 3);
    CHECK(write_data(&buffer, wrapped, sizeof(wrapped) - 1) == 0);
    CHECK(TcpRingBuffer_GetUsed(&buffer) == sizeof(storage));

    CHECK(TcpRingBuffer_GetWriteRegions(&buffer, regions) == 0);
    CHECK(TcpRingBuffer_WriteCommit(&buffer, 1) == -1);
    CHECK(check_read_data(&buffer, expected, sizeof(expected) - 1) == 0);
    CHECK(TcpRingBuffer_GetReadRegions(&buffer, regions) == 2);
    CHECK(regions[0].len == 4);
    CHECK(regions[1].len == 3);

    CHECK(TcpRingBuffer_ReadCommit(&buffer, 7) == 0);
    CHECK(TcpRingBuffer_GetReadable(&buffer) == 0);
    CHECK(TcpRingBuffer_GetQueued(&buffer) == 8);
    CHECK(TcpRingBuffer_GetReadRegions(&buffer, regions) == 0);

    CHECK(TcpRingBuffer_ReadRelease(&buffer, 1) == 0);
    CHECK(TcpRingBuffer_GetUsed(&buffer) == 7);
    CHECK(TcpRingBuffer_GetWritable(&buffer) == 1);
    CHECK(write_data(&buffer, final_byte, 1) == 0);

    CHECK(TcpRingBuffer_GetReadRegions(&buffer, regions) == 1);
    CHECK(regions[0].len == 1);
    CHECK(regions[0].data[0] == 'Z');
    CHECK(TcpRingBuffer_ReadCommit(&buffer, 1) == 0);
    CHECK(TcpRingBuffer_ReadRelease(&buffer, 8) == 0);

    CHECK(TcpRingBuffer_GetUsed(&buffer) == 0);
    CHECK(TcpRingBuffer_GetQueued(&buffer) == 0);
    CHECK(TcpRingBuffer_GetWritable(&buffer) == sizeof(storage));
    CHECK(TcpRingBuffer_GetReadable(&buffer) == 0);
    CHECK(TcpRingBuffer_ReadRelease(&buffer, 1) == -1);

    TcpRingBuffer_Reset(&buffer);
    CHECK(TcpRingBuffer_GetUsed(&buffer) == 0);
    CHECK(TcpRingBuffer_GetWritable(&buffer) == sizeof(storage));

    puts("TCP ring buffer tests passed");
    return 0;
}
