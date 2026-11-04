# Rust UDPGW upstream pin

- Upstream: https://github.com/Dean6767/RustBadVpnUdpgw
- Pinned commit: `bfd488a62618fc9dd026ac221b2f274a90ffe2b8`
- Pinned date: 2026-11-04
- License: MIT; upstream `LICENSE` is retained verbatim.
- Vendored snapshot: `impl/rust/upstream/`
- Local patch series: `impl/rust/patches/series`

The upstream snapshot is kept separate from RelayVPN integration changes. Do not edit files under
`upstream/` for local optimizations. Add each local change as a numbered unified patch under
`patches/`, list it in `patches/series`, and apply the series in CI/build scripts before compiling.

Included upstream files:
- `.gitattributes`
- `.gitignore`
- `Cargo.toml`
- `Cargo.lock`
- `LICENSE`
- `README.md`
- `contrib/badvpn-udpgw.service`
- `src/main.rs`
- `src/udpgw.rs`

The upstream GitHub Actions workflows are intentionally not copied; RelayVPN owns its CI.
No local patches are applied in this initial snapshot.
