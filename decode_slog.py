#!/usr/bin/env python3
"""Decode GaggiMate .slog shot logs (log versions 5-8) into CSV files.

Format: GaggiMate firmware src/display/models/shot_log_format.h

Usage:
    python decode_slog.py [--plot]                      # data/slog -> data/decoded
    python decode_slog.py <folder or .slog files> -o <out> [--plot]

Output:
    shots.csv           one row per shot (header + notes from <id>.json), rebuilt every run
    samples.csv         one row per sample, all shots (key: shot_id), rebuilt every run
    per_shot/<id>.csv   samples of one shot, only written if it does not exist yet
    per_shot/<id>.png   chart of one shot (with --plot), only written if it does not exist yet
"""

import argparse
import csv
import json
import struct
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = Path("data/slog")
DEFAULT_OUTPUT = Path("data/decoded")

MAGIC = 0x544F4853  # "SHOT"
HEADER_SIZE = 512
PHASES_OFFSET = 110
PHASE_SIZE = 29
MAX_PHASES = 12

EXIT_REASONS = ["unknown", "volumetric_target", "pressure_target", "flow_target", "pumped_target",
                "duration", "safety_timeout", "aborted", "hold_released", "ratio_target"]

# Sample fields after the timestamp, in file order: (column, struct format, divisor)
FIELDS = [
    ("target_temp_c", "H", 10), ("temp_c", "H", 10),
    ("target_pressure_bar", "H", 10), ("pressure_bar", "H", 10),
    ("pump_flow_mls", "h", 100), ("target_flow_mls", "h", 100),
    ("puck_flow_mls", "h", 100), ("weight_flow_gs", "h", 100),
    ("weight_g", "H", 10), ("est_weight_g", "H", 10),
    ("puck_resistance", "H", 100), ("si_raw", "H", 1),
    ("water_pumped_ml", "H", 10),  # v7+ only
]

SI_FLAGS = ["si_shot_started_volumetric", "si_currently_volumetric", "si_bt_scale_connected",
            "si_volumetric_available", "si_extended_recording", "si_process_is_brew",
            "si_target_is_volumetric", "si_phase_has_volumetric", "si_active_scale_connected"]

SAMPLE_COLUMNS = (["shot_id", "sample_idx", "t_ms", "t_s", "phase_number", "phase_name"]
                  + [name for name, _, _ in FIELDS] + SI_FLAGS)


def cstr(raw):
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def decode(data, shot_id):
    """Return (shot_info: dict, samples: list[dict])."""
    magic, version, sample_size = struct.unpack_from("<IBB", data, 0)
    if magic != MAGIC:
        raise ValueError("not a .slog file")
    if not 5 <= version <= 8:
        raise ValueError(f"log version {version} not supported (5-8 only)")
    fields_mask, count_hdr, duration_hdr, epoch = struct.unpack_from("<IIII", data, 12)

    # v5 and the legacy-scale v6 use a 2-byte tick (x 250 ms); later files use 4-byte elapsed ms
    tick_time = version == 5 or (version == 6 and sample_size == 26 and fields_mask == 0x1FFF)
    n_fields = 13 if version < 7 else 14
    time_fmt = "H" if tick_time else "I"
    sample_fmt = "<" + time_fmt + "".join(fmt for _, fmt, _ in FIELDS[:n_fields - 1])
    assert struct.calcsize(sample_fmt) == sample_size, f"sample size {sample_size} does not match layout"

    end = PHASES_OFFSET + MAX_PHASES * PHASE_SIZE
    n_phases, exit_reason, brew_delay_ms = struct.unpack_from("<BBH", data, end)
    phases = []
    for i in range(n_phases):
        offset = PHASES_OFFSET + i * PHASE_SIZE
        start_idx, number = struct.unpack_from("<HB", data, offset)
        phases.append((start_idx, number, cstr(data[offset + 4:offset + PHASE_SIZE])))

    n_in_file = (len(data) - HEADER_SIZE) // sample_size
    samples = []
    phase = (0, "Phase 1")
    for i in range(min(count_hdr, n_in_file) or n_in_file):
        values = struct.unpack_from(sample_fmt, data, HEADER_SIZE + i * sample_size)
        t_ms = values[0] * 250 if tick_time else values[0]
        for start_idx, number, name in phases:
            if i >= start_idx:
                phase = (number, name)
        row = {"shot_id": shot_id, "sample_idx": i, "t_ms": t_ms, "t_s": t_ms / 1000,
               "phase_number": phase[0], "phase_name": phase[1]}
        for (name, _, div), raw in zip(FIELDS, values[1:]):
            row[name] = raw / div if div != 1 else raw
        for bit, flag in enumerate(SI_FLAGS):
            row[flag] = (row["si_raw"] >> bit) & 1
        if version < 8:  # no separate active-scale bit before v8
            row["si_active_scale_connected"] = row["si_bt_scale_connected"]
        samples.append(row)

    incomplete = count_hdr == 0 or count_hdr != len(samples) or (len(data) - HEADER_SIZE) % sample_size != 0
    duration_ms = duration_hdr if duration_hdr and not incomplete else (samples[-1]["t_ms"] if samples else 0)
    final_weight = struct.unpack_from("<H", data, 108)[0] / 10 or (samples[-1]["weight_g"] if samples else None)

    shot = {
        "shot_id": shot_id,
        "version": version,
        "start_time_utc": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "profile_id": cstr(data[28:60]),
        "profile_name": cstr(data[60:108]),
        "duration_s": duration_ms / 1000,
        "sample_count": len(samples),
        "final_weight_g": final_weight,
        "final_exit_reason": EXIT_REASONS[exit_reason] if exit_reason < len(EXIT_REASONS) else exit_reason,
        "brew_delay_ms": brew_delay_ms,
        "incomplete": incomplete,
        "phases": " > ".join(name for _, _, name in phases),
    }
    return shot, samples


def load_notes(slog_path):
    """Notes are saved as <id>.json next to the .slog (padded or unpadded id)."""
    for path in (slog_path.with_suffix(".json"), slog_path.with_name((slog_path.stem.lstrip("0") or "0") + ".json")):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_csv(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def plot(shot, samples, path):
    from matplotlib.figure import Figure  # no pyplot: keeps the Jupyter plot backend untouched

    t = [s["t_s"] for s in samples]
    fig = Figure(figsize=(10, 5))
    ax1 = fig.subplots()
    ax2 = ax1.twinx()
    for ax, col, color, style in [
        (ax1, "pressure_bar", "tab:blue", "-"), (ax1, "target_pressure_bar", "tab:blue", "--"),
        (ax1, "pump_flow_mls", "tab:green", "-"), (ax1, "puck_flow_mls", "yellowgreen", "-"),
        (ax1, "weight_flow_gs", "tab:purple", "-"),
        (ax2, "temp_c", "tab:red", "-"), (ax2, "weight_g", "tab:brown", "-"),
    ]:
        ax.plot(t, [s[col] for s in samples], color=color, ls=style, lw=1.2, label=col)
    ax1.set_ylim(-0.5, 12)  # view only: hides start-up spikes such as weight flow -20 g/s
    for name in shot["phases"].split(" > "):
        first = next((s for s in samples if s["phase_name"] == name), None)
        if first:
            ax1.axvline(first["t_s"], color="grey", lw=0.6, ls=":")
            ax1.text(first["t_s"], 11.8, " " + name, fontsize=7, rotation=90, va="top", color="grey")
    ax1.set_xlabel("time (s)")
    ax1.set_ylabel("bar | ml/s | g/s")
    ax2.set_ylabel("°C | g")
    handles = ax1.get_legend_handles_labels()[0] + ax2.get_legend_handles_labels()[0]
    ax1.legend(handles, [h.get_label() for h in handles], fontsize=7, loc="upper left")
    ax1.set_title(f"Shot {shot['shot_id']} - {shot['profile_name']} - {shot['start_time_utc'][:16]} UTC")
    fig.tight_layout()
    fig.savefig(path, dpi=110)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Decode GaggiMate .slog shot logs to CSV")
    parser.add_argument("inputs", nargs="*", default=[DEFAULT_INPUT], help=f".slog files or folders (default: {DEFAULT_INPUT})")
    parser.add_argument("-o", "--out", default=DEFAULT_OUTPUT, help=f"output folder (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--plot", action="store_true", help="write a PNG chart per shot")
    args = parser.parse_args(argv)  # argv: list of args when called from Python/Jupyter

    files = []
    for p in map(Path, args.inputs):
        files += sorted(p.rglob("*.slog")) if p.is_dir() else [p]

    out = Path(args.out)
    (out / "per_shot").mkdir(parents=True, exist_ok=True)
    shots, all_samples = [], []
    new_count = 0
    for path in files:
        try:
            shot, samples = decode(path.read_bytes(), path.stem)
        except (ValueError, AssertionError, struct.error) as e:
            print(f"SKIPPED {path}: {e}")
            continue
        shot.update({f"notes_{k}": v for k, v in load_notes(path).items()})
        shots.append(shot)
        all_samples += samples
        shot_csv = out / "per_shot" / f"{path.stem}.csv"
        if not shot_csv.exists():
            write_csv(shot_csv, samples, SAMPLE_COLUMNS)
            new_count += 1
        shot_png = out / "per_shot" / f"{path.stem}.png"
        if args.plot and not shot_png.exists():
            plot(shot, samples, shot_png)

    shot_columns = list(dict.fromkeys(k for shot in shots for k in shot))
    write_csv(out / "shots.csv", shots, shot_columns)
    write_csv(out / "samples.csv", all_samples, SAMPLE_COLUMNS)
    print(f"Decoded {len(shots)}/{len(files)} shots ({new_count} new), {len(all_samples)} samples -> {out}/")


if __name__ == "__main__":
    main()
