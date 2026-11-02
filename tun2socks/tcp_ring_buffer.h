/*
 * TCP ring buffer primitives for bounded, zero-copy data handoff.
 */

#ifndef BADVPN_TUN2SOCKS_TCP_RING_BUFFER_H
#define BADVPN_TUN2SOCKS_TCP_RING_BUFFER_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint8_t *data;
    size_t len;
} TcpRingBufferRegion;

typedef struct {
    uint8_t *data;
    size_t capacity;
    size_t release_pos;
    size_t submit_pos;
    size_t write_pos;
    size_t used;
    size_t queued;
} TcpRingBuffer;

int TcpRingBuffer_Init(TcpRingBuffer *buffer, void *storage, size_t capacity);
void TcpRingBuffer_Reset(TcpRingBuffer *buffer);

size_t TcpRingBuffer_GetCapacity(const TcpRingBuffer *buffer);
size_t TcpRingBuffer_GetUsed(const TcpRingBuffer *buffer);
size_t TcpRingBuffer_GetWritable(const TcpRingBuffer *buffer);
size_t TcpRingBuffer_GetReadable(const TcpRingBuffer *buffer);
size_t TcpRingBuffer_GetQueued(const TcpRingBuffer *buffer);

int TcpRingBuffer_GetWriteRegions(
    TcpRingBuffer *buffer,
    TcpRingBufferRegion regions[2]
);
int TcpRingBuffer_WriteCommit(TcpRingBuffer *buffer, size_t length);

int TcpRingBuffer_GetReadRegions(
    TcpRingBuffer *buffer,
    TcpRingBufferRegion regions[2]
);
int TcpRingBuffer_ReadCommit(TcpRingBuffer *buffer, size_t length);
int TcpRingBuffer_ReadRelease(TcpRingBuffer *buffer, size_t length);

#endif
