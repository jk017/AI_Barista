"""Fetch shot annotations (notes) from GaggiMate over WebSocket.
 
Reads data/index.bin (written by sync_shots.py), asks the device for the notes of
every local shot that has the "has notes" flag, and saves them as
data/slog/<id>.json next to <id>.slog, where decode_slog.py picks them up.
 
Notes are fetched again on every run, because you can edit them after the shot
(for example add a rating). A file is only rewritten if its content changed.
"""
 
import asyncio
import json
import os
import pathlib
import struct
 
import websockets
from dotenv import load_dotenv
 
load_dotenv()
HOST_IP = os.getenv("GAGGIA_IP")
 
INDEX_MAGIC = 0x58444953  # "SIDX"
INDEX_HEADER_SIZE = 32
FLAG_DELETED = 0x02
FLAG_HAS_NOTES = 0x04
TIMEOUT_S = 5.0
 
 
def shots_with_notes(index_bytes: bytes) -> set[str]:
    """Return the padded ids of all shots in index.bin with notes and not deleted."""
    magic, _version, entry_size, count = struct.unpack_from("<IHHI", index_bytes, 0)
    if magic != INDEX_MAGIC:
        raise ValueError("index.bin has a bad magic number")
    ids = set()
    for i in range(count):
        offset = INDEX_HEADER_SIZE + i * entry_size
        (shot_id,) = struct.unpack_from("<I", index_bytes, offset)
        flags = index_bytes[offset + 15]  # after id, timestamp, duration (uint32) + volume (uint16) + rating (uint8)
        if flags & FLAG_HAS_NOTES and not flags & FLAG_DELETED:
            ids.add(f"{shot_id:06d}")
    return ids
 
 
async def fetch_notes(ws, shot_id: str) -> dict:
    """Ask the device for the notes of one shot. Skips other messages (status events)."""
    rid = f"notes-{shot_id}"
    await ws.send(json.dumps({"tp": "req:history:notes:get", "id": int(shot_id), "rid": rid}))
 
    async def wait_for_reply():
        async for message in ws:
            data = json.loads(message)
            if data.get("rid") == rid:
                notes = data.get("notes") or {}
                return json.loads(notes) if isinstance(notes, str) else notes
 
    return await asyncio.wait_for(wait_for_reply(), TIMEOUT_S)
 
 
async def fetch_annotations_async(data_dir: str | pathlib.Path = "data") -> list[str]:
    """Fetch notes for all local shots. Returns the ids whose notes are new or changed."""
    data_dir = pathlib.Path(data_dir)
    slog_dir = data_dir / "slog"
    index_path = data_dir / "index.bin"
    if not index_path.exists():
        print("no data/index.bin - run sync_shots.py first")
        return []
 
    local_ids = {p.stem for p in slog_dir.glob("*.slog")}
    ids = sorted(shots_with_notes(index_path.read_bytes()) & local_ids)
    print(f"shots with notes: {len(ids)}")
 
    changed: list[str] = []
    empty: list[str] = []
    async with websockets.connect(f"ws://{HOST_IP}/ws") as ws:
        for shot_id in ids:
            notes = await fetch_notes(ws, shot_id)
            if not notes:
                empty.append(shot_id)
                continue
            path = slog_dir / f"{shot_id}.json"
            text = json.dumps(notes, indent=2, ensure_ascii=False)
            if path.exists() and path.read_text(encoding="utf-8") == text:
                continue
            path.write_text(text, encoding="utf-8")
            changed.append(shot_id)
            print(f"  {shot_id}: notes saved")
    if empty:
        print(f"device returned empty notes for {len(empty)} shot(s): {empty[:5]}")
    print(f"unchanged: {len(ids) - len(changed) - len(empty)}, new or changed: {len(changed)}")
    return changed
 
 
if __name__ == "__main__":
    changed = asyncio.run(fetch_annotations_async())
    print(f"done - {len(changed)} new or changed annotation(s)")
 