#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path


async def handle(reader, writer, root):
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=120)
        if not line:
            return
        parts = line.decode("latin-1", "replace").strip().split()
        target = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"
        while True:
            header = await asyncio.wait_for(reader.readline(), timeout=30)
            if header in (b"\r\n", b"\n", b""):
                break

        if target == "/small.txt":
            body = b"RelayVPN phase2e benchmark probe\n"
            status = b"200 OK"
        elif target in ("/large.bin", "/large10.bin"):
            path = root / target.lstrip("/")
            body = None
            status = b"200 OK"
        else:
            body = b"not found\n"
            status = b"404 Not Found"

        if body is not None:
            writer.write(
                b"HTTP/1.1 " + status + b"\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\nConnection: close\r\n"
                + b"Content-Type: application/octet-stream\r\n\r\n" + body
            )
            await writer.drain()
        else:
            size = path.stat().st_size
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: " + str(size).encode()
                + b"\r\nConnection: close\r\nContent-Type: application/octet-stream\r\n\r\n"
            )
            await writer.drain()
            with path.open("rb") as source:
                while True:
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
    except (asyncio.TimeoutError, ConnectionError, BrokenPipeError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass


async def main(args):
    root = Path(args.root)
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, root),
        args.host,
        args.port,
        backlog=4096,
        limit=65536,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--host", default="198.51.100.10")
    parser.add_argument("--port", type=int, default=8080)
    asyncio.run(main(parser.parse_args()))
