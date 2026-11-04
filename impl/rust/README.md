# Rust UDPGW integration

The exact pinned upstream source is in `upstream/`. Build it with:

```sh
cargo test --locked --manifest-path impl/rust/upstream/Cargo.toml
cargo build --locked --release --manifest-path impl/rust/upstream/Cargo.toml
```

RelayVPN-specific modifications must be maintained as patch files under `patches/`, never edited
directly into the upstream snapshot. The patch series is currently empty.
