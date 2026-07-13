"""HTTP client for Gaggimate API

- Querry Shot Histroy 
"""

import sys
import time
import pathlib
import os
from dotenv import load_dotenv

import httpx

load_dotenv()
HOST_IP = os.getenv("GAGGIA_IP")

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.0

def is_html_response(data: bytes) -> bool:
    head = data[:256].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")

def fetch_histroy_index(host: str, timeout: float = 10.0) -> bytes | None:

    url = f"http://{host}/api/history/index.bin"
    

    for attempt in range(MAX_RETRIES):
        try:
            response = httpx.get(
                url,
                headers={"Accept": "application/octet-stream"},
                timeout=timeout
            )
        except httpx.TimeoutException:
            if attempt == MAX_RETRIES -1:
                raise
            wait = RETRY_BACKOFF_BASE * (2**attempt)
            print(f"timeout, retrying in {wait:.0f}s ...")
            time.sleep(wait)
            continue

        if response.status_code == 404:
            return None
        
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.reason_phrase}")
        
        data = response.content

        if is_html_response(data):
            if attempt == MAX_RETRIES -1:
                raise RuntimeError(
                    "Device kept returning HTML instead of binary index data"
                    "(likely overloaded). Try again in a moment."
                )
            wait = RETRY_BACKOFF_BASE * (2**attempt)
            print(f"got HTML instead of binary, retrying in {wait:.0f}s ...")
            time.sleep(wait)
            continue

        return data

    raise RuntimeError("unreachable")

if __name__ == "__main__":
    data = fetch_histroy_index(HOST_IP)

    if data is None:
        print("device reachable, but no shot history yet (404)")
    else:
        out = pathlib.Path("data/index.bin")
        out.write_bytes(data)
        print(f"fetched {len(data)} bytes -> {out.resolve()}")
        print(f"first 32 bytes (hex): {data[:32].hex(' ')}")
