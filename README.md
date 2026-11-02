**This repository has been archived and is not being maintained by the author any longer.**

# BadVPN

## Introduction

In this project I host some of my open-source networking software.
All of the software is written in C and utilizes a custom-developed framework for event-driven programming.
The extensive code sharing is the reason all the software is packaged together.
However, it is possible to compile only the required components to avoid extra dependencies.

### NCD programming language

NCD (Network Configuration Daemon) is a daemon and programming/scripting language for configuration of network interfaces and other aspects of the operating system.
It implements various functionalities as built-in modules, which may be used from an NCD program wherever and for whatever purpose the user needs them.
This modularity makes NCD extremely flexible and extensible. It does a very good job with hotplugging in various forms, like USB network interfaces and link detection for wired devices.
New features can be added by implementing statements as C-language modules using a straightforward interface.

### Tun2socks network-layer proxifier

The tun2socks program "socksifes" TCP connections at the network layer.
It implements a TUN device which accepts all incoming TCP connections (regardless of destination IP), and forwards the connections through a SOCKS server.
This allows you to forward all connections through SOCKS, without any need for application support.
It can be used, for example, to forward connections through a remote SSH server.

### Peer-to-peer VPN

The VPN part of this project implements a Layer 2 (Ethernet) network between the peers (VPN nodes).
The peers connect to a central server which acts as a communication proxy allowing the peers to establish direct connections between each other (data connections).
These connections are used for transferring network data (Ethernet frames), and can be secured with a multitude of mechanisms. Notable features are:

- UDP and TCP transport
- Converges very quickly after a new peer joins
- IGMP snooping to deliver multicasts efficiently (e.g. for IPTV)
- Double SSL: if SSL is enabled, not only do peers connect to the server with SSL, but they use an additional layer of SSL when exchanging messages through the server
- Features related to the NAT problem:
  - Can work with multiple layers of NAT (needs configuration)
  - Local peers inside a NAT can communicate directly
  - Relaying as a fallback (needs configuration)

## Requirements

NCD only works on Linux. Tun2socks works on Linux and Windows. The P2P VPN works on Linux, Windows and FreeBSD (not tested often).

## Installation

The build system is based on CMake. On Linux, the following commands can be used to
build:

```
cd <badvpn-source-dir>
mkdir build
cd build
cmake .. -DCMAKE_INSTALL_PREFIX=<install-dir>
make install
```

If you only need tun2socks or udpgw, then add the following arguments to the `cmake`
command: `-DBUILD_NOTHING_BY_DEFAULT=1 -DBUILD_TUN2SOCKS=1 -DBUILD_UDPGW=1`.
Otherwise (if you want the VPN software), you will first need to install the OpenSSL
and NSS libraries and make sure that CMake can find them.

Windows builds are not provided. You can build from source code using Visual Studio by
following the instructions in the file `BUILD-WINDOWS-VisualStudio.md`.


## Engine capabilities

| Engine | TCP transport | UDP transport | Configuration |
| --- | --- | --- | --- |
| BadVPN | SOCKS5 TCP | UDPGW or SOCKS5 UDP (`--socks5-udp`) | Native command-line options |
| Hev | SOCKS5 TCP | SOCKS5 UDP ASSOCIATE | YAML configuration |

The Phase 1d runtime check validates a TUN-to-Hev-to-SOCKS5 UDP ASSOCIATE round trip against a test proxy. It does not validate the separate BadVPN UDPGW path.

## Tun2socks engines

RelayVPN provides two independent tun2socks engines. Build both on Linux with:

```bash
cmake -S . -B build -DBUILD_NOTHING_BY_DEFAULT=ON -DBUILD_TUN2SOCKS=ON -DBUILD_UDPGW=ON -DBUILD_HEV=ON
cmake --build build --parallel
cmake --install build
```

Use the dispatcher to select an engine while keeping each engine's native arguments and configuration:

```bash
relayvpn badvpn --tundev tun0 --netif-ipaddr 198.18.0.1 --netif-netmask 255.255.255.0 --socks-server-addr 127.0.0.1:1080
relayvpn hev /path/to/hev.yml
```

BadVPN retains its existing CLI options and UDPGW/SOCKS UDP modes. Hev uses its own YAML configuration and native SOCKS5 TCP/UDP support. The engines are alternatives for a TUN interface: run only one engine against a given TUN interface at a time.

The dispatcher searches beside itself first, then the repository build output and `PATH`. Override binary locations with `RELAYVPN_BADVPN_BIN` or `RELAYVPN_HEV_BIN`. The current RelayVPN CMake integration builds Hev on native Linux; upstream Hev supports additional platforms independently.

Run `relayvpn-doctor` after installation to inspect engine binaries, shared-library resolution, TUN-device availability, and basic privilege requirements. The doctor reports missing TUN access as a warning because Android VPNService and other VPN hosts may provide a TUN file descriptor without exposing `/dev/net/tun`.

## License

The BSD 3-clause license as shown below applies to most of the code.

```
Copyright (c) 2009, Ambroz Bizjak <ambrop7@gmail.com>
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.
3. Neither the name of the author nor the
   names of its contributors may be used to endorse or promote products
   derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

List of third-party code included in the source:
- lwIP - A Lightweight TCP/IP stack. License: `lwip/COPYING`
