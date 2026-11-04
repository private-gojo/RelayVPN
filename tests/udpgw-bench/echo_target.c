#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define MAGIC "RVBG"
#define META_LEN 25
#define BATCH 64
#define MAX_PACKET 65535

typedef struct {
    struct sockaddr_storage addr;
    socklen_t addr_len;
    int active;
} peer_t;

static volatile sig_atomic_t stopping = 0;

static void on_signal(int sig) {
    (void)sig;
    stopping = 1;
}

static uint64_t mono_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static uint32_t get_u32le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put_u32le(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static void put_u64le(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i));
}

static int parse_int(const char *s, int min, int max) {
    char *end = NULL;
    long v = strtol(s, &end, 10);
    if (!s[0] || !end || *end || v < min || v > max) {
        fprintf(stderr, "invalid integer: %s\n", s);
        exit(2);
    }
    return (int)v;
}

int main(int argc, char **argv) {
    int port = 0, max_flows = 10000, payload_size = 64;
    int mode = 0;
    uint64_t rate_pps = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--port") && i + 1 < argc) port = parse_int(argv[++i], 0, 65535);
        else if (!strcmp(argv[i], "--max-flows") && i + 1 < argc) max_flows = parse_int(argv[++i], 1, 1000000);
        else if (!strcmp(argv[i], "--payload-size") && i + 1 < argc) payload_size = parse_int(argv[++i], META_LEN, 65507);
        else if (!strcmp(argv[i], "--rate-pps") && i + 1 < argc) rate_pps = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) {
            const char *m = argv[++i];
            if (!strcmp(m, "up")) mode = 1;
            else if (!strcmp(m, "down")) mode = 2;
            else if (!strcmp(m, "bidi")) mode = 3;
            else { fprintf(stderr, "invalid mode: %s\n", m); return 2; }
        } else {
            fprintf(stderr, "unknown or incomplete argument: %s\n", argv[i]);
            return 2;
        }
    }

    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) { perror("socket"); return 1; }
    int bufsize = 16 * 1024 * 1024;
    setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &bufsize, sizeof(bufsize));
    setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &bufsize, sizeof(bufsize));

    struct sockaddr_in bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    bind_addr.sin_port = htons((uint16_t)port);
    if (bind(fd, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
        perror("bind"); close(fd); return 1;
    }
    socklen_t bind_len = sizeof(bind_addr);
    if (getsockname(fd, (struct sockaddr *)&bind_addr, &bind_len) < 0) {
        perror("getsockname"); close(fd); return 1;
    }

    peer_t *peers = calloc((size_t)max_flows, sizeof(*peers));
    uint8_t *in_storage = calloc(BATCH, MAX_PACKET);
    struct sockaddr_storage *from = calloc(BATCH, sizeof(*from));
    struct iovec *in_iov = calloc(BATCH, sizeof(*in_iov));
    struct mmsghdr *in_msg = calloc(BATCH, sizeof(*in_msg));
    uint8_t *out_storage = calloc(BATCH, (size_t)payload_size);
    struct iovec *out_iov = calloc(BATCH, sizeof(*out_iov));
    struct mmsghdr *out_msg = calloc(BATCH, sizeof(*out_msg));
    if (!peers || !in_storage || !from || !in_iov || !in_msg ||
        !out_storage || !out_iov || !out_msg) {
        fprintf(stderr, "allocation failed\n"); return 1;
    }

    for (int i = 0; i < BATCH; i++) {
        in_iov[i].iov_base = in_storage + (size_t)i * MAX_PACKET;
        in_iov[i].iov_len = MAX_PACKET;
        in_msg[i].msg_hdr.msg_iov = &in_iov[i];
        in_msg[i].msg_hdr.msg_iovlen = 1;
        in_msg[i].msg_hdr.msg_name = &from[i];
        in_msg[i].msg_hdr.msg_namelen = sizeof(from[i]);

        out_iov[i].iov_base = out_storage + (size_t)i * payload_size;
        out_iov[i].iov_len = (size_t)payload_size;
        out_msg[i].msg_hdr.msg_iov = &out_iov[i];
        out_msg[i].msg_hdr.msg_iovlen = 1;
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    uint64_t rx_data = 0, rx_bytes = 0, setup_count = 0;
    uint64_t probe_count = 0, echo_tx = 0, down_tx = 0, send_errors = 0;
    uint64_t down_seq = 0, next_due = mono_ns();
    uint32_t next_peer = 0;
    int active_peers = 0, down_started = 0;

    printf("READY %u\n", (unsigned)ntohs(bind_addr.sin_port));
    fflush(stdout);

    while (!stopping) {
        struct pollfd pfd = {.fd = fd, .events = POLLIN};
        struct timespec wait_ts;
        struct timespec *wait_ptr = NULL;
        int timeout = (mode == 2 && down_started && active_peers > 0 && rate_pps == 0) ? 0 : 10;
        if (mode == 2 && down_started && active_peers > 0 && rate_pps > 0) {
            uint64_t interval = 1000000000ull / rate_pps;
            if (interval == 0) interval = 1;
            uint64_t wait_ns = interval * BATCH;
            if (wait_ns > 10000000ull) wait_ns = 10000000ull;
            wait_ts.tv_sec = (time_t)(wait_ns / 1000000000ull);
            wait_ts.tv_nsec = (long)(wait_ns % 1000000000ull);
            wait_ptr = &wait_ts;
        }
        int pr = wait_ptr ? ppoll(&pfd, 1, wait_ptr, NULL) : poll(&pfd, 1, timeout);
        if (pr < 0 && errno != EINTR) { perror("poll"); break; }

        if (pr > 0 && (pfd.revents & POLLIN)) {
            int n;
            do {
                for (int i = 0; i < BATCH; i++) {
                    in_msg[i].msg_hdr.msg_namelen = sizeof(from[i]);
                    in_msg[i].msg_len = 0;
                }
                n = recvmmsg(fd, in_msg, BATCH, MSG_DONTWAIT, NULL);
                if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                    perror("recvmmsg"); stopping = 1; break;
                }
                if (n <= 0) break;

                int out_count = 0;
                for (int i = 0; i < n; i++) {
                    uint8_t *pkt = in_iov[i].iov_base;
                    size_t len = in_msg[i].msg_len;
                    if (len < META_LEN || memcmp(pkt, MAGIC, 4) != 0) continue;
                    char kind = (char)pkt[4];
                    uint32_t token = get_u32le(pkt + 5);
                    if (kind == 'S') {
                        if (token < (uint32_t)max_flows && !peers[token].active) {
                            memcpy(&peers[token].addr, &from[i], in_msg[i].msg_hdr.msg_namelen);
                            peers[token].addr_len = in_msg[i].msg_hdr.msg_namelen;
                            peers[token].active = 1;
                            active_peers++;
                            setup_count++;
                        }
                        uint8_t *out = out_storage + (size_t)out_count * payload_size;
                        memcpy(out, pkt, len);
                        out[4] = 'A';
                        put_u64le(out + 17, mono_ns());
                        out_iov[out_count].iov_len = len;
                        out_msg[out_count].msg_hdr.msg_name = &from[i];
                        out_msg[out_count].msg_hdr.msg_namelen = in_msg[i].msg_hdr.msg_namelen;
                        out_count++;
                    } else if (kind == 'G') {
                        down_started = 1;
                    } else if (kind == 'P') {
                        probe_count++;
                        uint8_t *out = out_storage + (size_t)out_count * payload_size;
                        memcpy(out, pkt, len);
                        out_iov[out_count].iov_len = len;
                        out_msg[out_count].msg_hdr.msg_name = &from[i];
                        out_msg[out_count].msg_hdr.msg_namelen = in_msg[i].msg_hdr.msg_namelen;
                        out_count++;
                    } else if (kind == 'D') {
                        rx_data++;
                        rx_bytes += len;
                        if (mode == 3) {
                            uint8_t *out = out_storage + (size_t)out_count * payload_size;
                            memcpy(out, pkt, len);
                            out_iov[out_count].iov_len = len;
                            out_msg[out_count].msg_hdr.msg_name = &from[i];
                            out_msg[out_count].msg_hdr.msg_namelen = in_msg[i].msg_hdr.msg_namelen;
                            out_count++;
                        }
                    }
                    if (out_count == BATCH) {
                        int sent = sendmmsg(fd, out_msg, (unsigned)out_count, MSG_DONTWAIT);
                        if (sent > 0) echo_tx += (uint64_t)sent;
                        if (sent < out_count) send_errors += (uint64_t)(out_count - (sent > 0 ? sent : 0));
                        out_count = 0;
                    }
                }
                if (out_count) {
                    int sent = sendmmsg(fd, out_msg, (unsigned)out_count, MSG_DONTWAIT);
                    if (sent > 0) echo_tx += (uint64_t)sent;
                    if (sent < out_count) send_errors += (uint64_t)(out_count - (sent > 0 ? sent : 0));
                }
            } while (n == BATCH);
        }

        if (mode == 2 && down_started && active_peers > 0) {
            uint64_t now = mono_ns();
            int due = 0;
            if (rate_pps == 0) {
                due = BATCH;
            } else {
                uint64_t interval = 1000000000ull / rate_pps;
                if (interval == 0) interval = 1;
                while (due < BATCH && now >= next_due) {
                    due++;
                    next_due += interval;
                }
            }
            int count = 0;
            for (int i = 0; i < due && count < BATCH; i++) {
                for (int scan = 0; scan < max_flows && count < BATCH; scan++) {
                    uint32_t token = next_peer++ % (uint32_t)max_flows;
                    if (!peers[token].active) continue;
                    uint8_t *pkt = out_storage + (size_t)count * payload_size;
                    memset(pkt, 0x5a, (size_t)payload_size);
                    memcpy(pkt, MAGIC, 4);
                    pkt[4] = 'D';
                    put_u32le(pkt + 5, token);
                    put_u64le(pkt + 9, ++down_seq);
                    put_u64le(pkt + 17, mono_ns());
                    out_iov[count].iov_len = (size_t)payload_size;
                    out_msg[count].msg_hdr.msg_name = &peers[token].addr;
                    out_msg[count].msg_hdr.msg_namelen = peers[token].addr_len;
                    count++;
                }
                if (count == 0) break;
            }
            if (count) {
                int sent = sendmmsg(fd, out_msg, (unsigned)count, MSG_DONTWAIT);
                if (sent > 0) down_tx += (uint64_t)sent;
                if (sent < count) send_errors += (uint64_t)(count - (sent > 0 ? sent : 0));
            }
        }
    }

    printf("{\"rx_data\":%llu,\"rx_bytes\":%llu,\"setup\":%llu,"
           "\"active_flows\":%d,\"probes\":%llu,\"echo_tx\":%llu,"
           "\"down_tx\":%llu,\"send_errors\":%llu}\n",
           (unsigned long long)rx_data, (unsigned long long)rx_bytes,
           (unsigned long long)setup_count, active_peers,
           (unsigned long long)probe_count, (unsigned long long)echo_tx,
           (unsigned long long)down_tx, (unsigned long long)send_errors);

    close(fd);
    free(peers); free(in_storage); free(from); free(in_iov); free(in_msg);
    free(out_storage); free(out_iov); free(out_msg);
    return 0;
}
