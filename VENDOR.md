# Vendored upstream sources

The Hev implementation is stored as ordinary source files under
`third_party/hev-socks5-tunnel/`. It is not a Git submodule. No upstream
source patches are applied in step 1a.

| Vendored path | Upstream repository | Pinned commit |
| --- | --- | --- |
| `third_party/hev-socks5-tunnel/` | https://github.com/heiher/hev-socks5-tunnel | `1711a443d72d89679c0a2044f36085358a101bc4` |
| `src/core/` | https://github.com/heiher/hev-socks5-core | `d12ec5962cbb777e087015db6aa73374014dd82e` |
| `third-part/hev-task-system/` | https://github.com/heiher/hev-task-system | `83196e8286c07e9eb174505eea4d076a6503ab48` |
| `third-part/lwip/` | https://github.com/heiher/lwip | `e274b3bea0877611773b5869a63c737ef231e841` |
| `third-part/yaml/` | https://github.com/heiher/yaml | `7e2ecb0e67995d424b35e436f846078d37904b4e` |

The four nested source directories above are the exact Git submodule revisions
recorded by the pinned Hev commit. The import script verifies each revision,
copies the checked-out contents, removes Git metadata, and stores the original
submodule mapping as `UPSTREAM.gitmodules`. The Hev commit is also recorded in
`.rev-id` for the upstream Makefile's build metadata.

Hev's tracked `third-part/wintun/` directory is retained as part of the Hev
snapshot; it is not one of the four Git submodules. Its `LICENSE.txt` is kept.
All upstream license files in Hev and the four pinned dependencies are
preserved. The lwIP license is BSD-style; Hev, core, task-system, and yaml
carry the included MIT license text.

BadVPN's existing source tree and `COPYING` are not replaced or removed.
