import sys
import pathlib
import os
from dotenv import load_dotenv

import httpx

from fetch_history import fetch_histroy_index, is_html_response

load_dotenv()
HOST_IP = os.getenv("GAGGIA_IP")

try:
    from parsers.index import parse_binary_index, index_to_shot_list
except ImportError:
    parse_binary_index = None

def index_shot_ids(index_bytes: bytes) -> set[str]:
    if parse_binary_index is None:
        raise NotImplementedError(
            "Copy parsers/index.py (and its imports) from "
            "https://github.com/julianleopold/gaggimate-mcp into ./parsers/ "
            "— it defines parse_binary_index / index_to_shot_list."
        )
    shots = index_to_shot_list(parse_binary_index(index_bytes))
    return {str(s["id"]).zfill(6) for s in shots}

def fetch_slog(host: str, shot_id: str, timeout: float = 10.0) -> bytes | None:
    """Download one .slog file. Returns None on 404 (shot gone from device)."""
    url = f"http://{host}/api/history/{shot_id}.slog"
    resp = httpx.get(url, headers={"Accept": "application/octet-stream"}, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if is_html_response(resp.content):
        raise RuntimeError(f"device returned HTML for shot {shot_id}, retry later")
    return resp.content


def sync_shots(data_dir: str | pathlib.Path = "data") -> list[str]:   
    data_dir = pathlib.Path(data_dir)
    slog_dir = data_dir / "slog"
    slog_dir.mkdir(parents=True, exist_ok=True)
    index_path = data_dir / "index.bin"
    
    new_index = fetch_histroy_index(HOST_IP)
    if new_index is None:
        print("device has no shot history yet")
        return []
    
    if index_path.exists() and index_path.read_bytes() == new_index:
        print("index unchanged - nothing to do")
        return []
    
    device_ids = index_shot_ids(new_index)
    local_ids = {p.stem for p in slog_dir.glob("*.slog")}
    missing = sorted(device_ids -local_ids)
    print(f"device: {len(device_ids)} shots, local: {len(local_ids)}, new: {len(missing)}")
    
    downloaded: list[str] = []
    for shot_id in missing:
        data = fetch_slog(HOST_IP, shot_id)
        if data is None:
            print(f"  {shot_id}: gone from device (rotated out), skipping")
            continue

        tmp = slog_dir / f"{shot_id}.slog.part"
        tmp.write_bytes(data)
        tmp.rename(slog_dir / f"{shot_id}.slog")
        downloaded.append(shot_id)
        print(f"  {shot_id}: {len(data)} bytes")


    index_path.write_bytes(new_index)
    return downloaded

if __name__ == "__main__":
    new = sync_shots()
    print(f"done — {len(new)} new shot(s)")



