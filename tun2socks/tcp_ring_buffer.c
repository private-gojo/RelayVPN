/*
 * TCP ring buffer primitives for bounded, zero-copy data handoff.
 */

#include "tcp_ring_buffer.h"

static int buffer_is_valid(const TcpRingBuffer *buffer)
{
    return buffer &&
           buffer->data &&
           buffer->capacity > 0 &&
           buffer->release_pos < buffer->capacity &&
           buffer->submit_pos < buffer->capacity &&
           buffer->write_pos < buffer->capacity &&
           buffer->used <= buffer->capacity &&
           buffer->queued <= buffer->used;
}

static size_t advance_position(size_t position, size_t length, size_t capacity)
{
    size_t tail = capacity - position;

    if (length >= tail) {
        return length - tail;
    }

    return position + length;
}

static void clear_regions(TcpRingBufferRegion regions[2])
{
    if (regions) {
        regions[0].data = NULL;
        regions[0].len = 0;
        regions[1].data = NULL;
        regions[1].len = 0;
    }
}

int TcpRingBuffer_Init(TcpRingBuffer *buffer, void *storage, size_t capacity)
{
    if (!buffer || !storage || capacity == 0) {
        return -1;
    }

    buffer->data = (uint8_t *)storage;
    buffer->capacity = capacity;
    buffer->release_pos = 0;
    buffer->submit_pos = 0;
    buffer->write_pos = 0;
    buffer->used = 0;
    buffer->queued = 0;

    return 0;
}

void TcpRingBuffer_Reset(TcpRingBuffer *buffer)
{
    if (!buffer_is_valid(buffer)) {
        return;
    }

    buffer->release_pos = 0;
    buffer->submit_pos = 0;
    buffer->write_pos = 0;
    buffer->used = 0;
    buffer->queued = 0;
}

size_t TcpRingBuffer_GetCapacity(const TcpRingBuffer *buffer)
{
    return buffer_is_valid(buffer) ? buffer->capacity : 0;
}

size_t TcpRingBuffer_GetUsed(const TcpRingBuffer *buffer)
{
    return buffer_is_valid(buffer) ? buffer->used : 0;
}

size_t TcpRingBuffer_GetWritable(const TcpRingBuffer *buffer)
{
    return buffer_is_valid(buffer) ? buffer->capacity - buffer->used : 0;
}

size_t TcpRingBuffer_GetReadable(const TcpRingBuffer *buffer)
{
    return buffer_is_valid(buffer) ? buffer->used - buffer->queued : 0;
}

size_t TcpRingBuffer_GetQueued(const TcpRingBuffer *buffer)
{
    return buffer_is_valid(buffer) ? buffer->queued : 0;
}

int TcpRingBuffer_GetWriteRegions(
    TcpRingBuffer *buffer,
    TcpRingBufferRegion regions[2]
)
{
    size_t available;
    size_t first;

    clear_regions(regions);

    if (!buffer_is_valid(buffer) || !regions) {
        return -1;
    }

    available = buffer->capacity - buffer->used;
    if (available == 0) {
        return 0;
    }

    first = buffer->capacity - buffer->write_pos;
    if (first > available) {
        first = available;
    }

    regions[0].data = buffer->data + buffer->write_pos;
    regions[0].len = first;

    if (available > first) {
        regions[1].data = buffer->data;
        regions[1].len = available - first;
        return 2;
    }

    return 1;
}

int TcpRingBuffer_WriteCommit(TcpRingBuffer *buffer, size_t length)
{
    if (!buffer_is_valid(buffer) || length > buffer->capacity - buffer->used) {
        return -1;
    }

    buffer->write_pos = advance_position(
        buffer->write_pos,
        length,
        buffer->capacity
    );
    buffer->used += length;

    return 0;
}

int TcpRingBuffer_GetReadRegions(
    TcpRingBuffer *buffer,
    TcpRingBufferRegion regions[2]
)
{
    size_t available;
    size_t first;

    clear_regions(regions);

    if (!buffer_is_valid(buffer) || !regions) {
        return -1;
    }

    available = buffer->used - buffer->queued;
    if (available == 0) {
        return 0;
    }

    first = buffer->capacity - buffer->submit_pos;
    if (first > available) {
        first = available;
    }

    regions[0].data = buffer->data + buffer->submit_pos;
    regions[0].len = first;

    if (available > first) {
        regions[1].data = buffer->data;
        regions[1].len = available - first;
        return 2;
    }

    return 1;
}

int TcpRingBuffer_ReadCommit(TcpRingBuffer *buffer, size_t length)
{
    if (!buffer_is_valid(buffer) ||
        length > buffer->used - buffer->queued) {
        return -1;
    }

    buffer->submit_pos = advance_position(
        buffer->submit_pos,
        length,
        buffer->capacity
    );
    buffer->queued += length;

    return 0;
}

int TcpRingBuffer_ReadRelease(TcpRingBuffer *buffer, size_t length)
{
    if (!buffer_is_valid(buffer) || length > buffer->queued) {
        return -1;
    }

    buffer->release_pos = advance_position(
        buffer->release_pos,
        length,
        buffer->capacity
    );
    buffer->queued -= length;
    buffer->used -= length;

    return 0;
}
