#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t stop_requested;

static void handle_signal(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

int main(void)
{
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) {
        perror("socket");
        return 1;
    }

    struct sockaddr_in local;
    memset(&local, 0, sizeof(local));
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    local.sin_port = 0;
    if (bind(fd, (struct sockaddr *)&local, sizeof(local)) < 0) {
        perror("bind");
        close(fd);
        return 1;
    }

    socklen_t local_len = sizeof(local);
    if (getsockname(fd, (struct sockaddr *)&local, &local_len) < 0) {
        perror("getsockname");
        close(fd);
        return 1;
    }

    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = handle_signal;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) < 0 ||
        sigaction(SIGINT, &action, NULL) < 0) {
        perror("sigaction");
        close(fd);
        return 1;
    }

    printf("%u\n", (unsigned int)ntohs(local.sin_port));
    fflush(stdout);

    unsigned char buffer[65535];
    while (!stop_requested) {
        struct sockaddr_in peer;
        socklen_t peer_len = sizeof(peer);
        ssize_t received = recvfrom(fd, buffer, sizeof(buffer), 0,
                                    (struct sockaddr *)&peer, &peer_len);
        if (received < 0) {
            if (errno == EINTR && stop_requested) {
                break;
            }
            if (errno == EINTR) {
                continue;
            }
            perror("recvfrom");
            close(fd);
            return 1;
        }
        ssize_t sent = sendto(fd, buffer, (size_t)received, 0,
                              (struct sockaddr *)&peer, peer_len);
        if (sent < 0 && errno != EINTR) {
            perror("sendto");
            close(fd);
            return 1;
        }
    }

    close(fd);
    return 0;
}
