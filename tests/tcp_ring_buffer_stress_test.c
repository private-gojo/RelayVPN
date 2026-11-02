#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../tun2socks/tcp_ring_buffer.h"

#define CAPACITY 257
#define OPERATIONS 1000000U
#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        return 1; \
    } \
} while (0)

static uint32_t random_state = 0x7a31d5c9U;

static uint32_t next_random(void)
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state;
}

static size_t random_amount(size_t maximum)
{
    return maximum ? (size_t)(next_random() % maximum) + 1 : 0;
}

static int check_state(
    TcpRingBuffer *buffer,
    const uint8_t storage[CAPACITY],
    const uint8_t model[CAPACITY],
    size_t head,
    size_t used,
    size_t queued
)
{
    TcpRingBufferRegion regions[2];
    size_t available;
    size_t position;
    size_t first;
    size_t offset;
    int count;
    int expected_count;
    int i;

    if (TcpRingBuffer_GetCapacity(buffer) != CAPACITY ||
        TcpRingBuffer_GetUsed(buffer) != used ||
        TcpRingBuffer_GetQueued(buffer) != queued ||
        TcpRingBuffer_GetReadable(buffer) != used - queued ||
        TcpRingBuffer_GetWritable(buffer) != CAPACITY - used) {
        return -1;
    }

    available = CAPACITY - used;
    position = (head + used) % CAPACITY;
    first = CAPACITY - position;
    if (first > available) {
        first = available;
    }
    expected_count = available == 0 ? 0 : (available > first ? 2 : 1);
    count = TcpRingBuffer_GetWriteRegions(buffer, regions);
    if (count != expected_count) {
        return -1;
    }
    if (count > 0 &&
        (regions[0].data != storage + position || regions[0].len != first)) {
        return -1;
    }
    if (count > 1 &&
        (regions[1].data != storage || regions[1].len != available - first)) {
        return -1;
    }

    available = used - queued;
    position = (head + queued) % CAPACITY;
    first = CAPACITY - position;
    if (first > available) {
        first = available;
    }
    expected_count = available == 0 ? 0 : (available > first ? 2 : 1);
    count = TcpRingBuffer_GetReadRegions(buffer, regions);
    if (count != expected_count) {
        return -1;
    }
    if (count > 0 &&
        (regions[0].data != storage + position || regions[0].len != first)) {
        return -1;
    }
    if (count > 1 &&
        (regions[1].data != storage || regions[1].len != available - first)) {
        return -1;
    }

    offset = 0;
    for (i = 0; i < count; i++) {
        size_t j;
        for (j = 0; j < regions[i].len; j++) {
            if (regions[i].data[j] != model[(head + queued + offset) % CAPACITY]) {
                return -1;
            }
            offset++;
        }
    }

    return offset == available ? 0 : -1;
}

int main(void)
{
    uint8_t storage[CAPACITY];
    uint8_t model[CAPACITY];
    TcpRingBuffer buffer;
    size_t head = 0;
    size_t used = 0;
    size_t queued = 0;
    unsigned int operation;

    memset(storage, 0, sizeof(storage));
    memset(model, 0, sizeof(model));

    CHECK(TcpRingBuffer_Init(&buffer, storage, sizeof(storage)) == 0);
    CHECK(TcpRingBuffer_WriteCommit(&buffer, CAPACITY + 1) == -1);
    CHECK(TcpRingBuffer_ReadCommit(&buffer, 1) == -1);
    CHECK(TcpRingBuffer_ReadRelease(&buffer, 1) == -1);

    for (operation = 0; operation < OPERATIONS; operation++) {
        uint32_t choice = next_random() % 100U;

        if (choice < 40U && used < CAPACITY) {
            TcpRingBufferRegion regions[2];
            size_t amount = random_amount(CAPACITY - used);
            size_t remaining = amount;
            size_t offset = 0;
            int count = TcpRingBuffer_GetWriteRegions(&buffer, regions);
            int i;

            CHECK(count > 0);
            for (i = 0; i < count && remaining > 0; i++) {
                size_t chunk = regions[i].len < remaining
                    ? regions[i].len
                    : remaining;
                size_t j;

                for (j = 0; j < chunk; j++) {
                    uint8_t value = (uint8_t)next_random();
                    regions[i].data[j] = value;
                    model[(head + used + offset) % CAPACITY] = value;
                    offset++;
                }
                remaining -= chunk;
            }
            CHECK(remaining == 0);
            CHECK(TcpRingBuffer_WriteCommit(&buffer, amount) == 0);
            used += amount;
        } else if (choice < 75U && used > queued) {
            size_t amount = random_amount(used - queued);
            CHECK(TcpRingBuffer_ReadCommit(&buffer, amount) == 0);
            queued += amount;
        } else if (queued > 0) {
            size_t amount = random_amount(queued);
            CHECK(TcpRingBuffer_ReadRelease(&buffer, amount) == 0);
            head = (head + amount) % CAPACITY;
            used -= amount;
            queued -= amount;
        }

        CHECK(check_state(&buffer, storage, model, head, used, queued) == 0);
    }

    while (used > 0) {
        if (used > queued) {
            size_t amount = used - queued;
            CHECK(TcpRingBuffer_ReadCommit(&buffer, amount) == 0);
            queued += amount;
        }
        CHECK(TcpRingBuffer_ReadRelease(&buffer, queued) == 0);
        head = (head + queued) % CAPACITY;
        used -= queued;
        queued = 0;
        CHECK(check_state(&buffer, storage, model, head, used, queued) == 0);
    }

    TcpRingBuffer_Reset(&buffer);
    CHECK(TcpRingBuffer_GetUsed(&buffer) == 0);
    CHECK(TcpRingBuffer_GetWritable(&buffer) == CAPACITY);

    printf(
        "TCP ring buffer randomized stress passed: %u operations, seed=0x7a31d5c9\n",
        OPERATIONS
    );
    return 0;
}
