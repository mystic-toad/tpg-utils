#!/usr/bin/env python3
"""
photofind.py - index and search photos by GPS location

Usage:
    python photofind.py index  <directory> [--out index.pkl]
    python photofind.py search <lat> <lon> [--index index.pkl] [--out results.json]
    python photofind.py combine <index1.pkl> <index2.pkl> ... [--out merged.pkl]

Dependencies:
    pip install -r requirements.txt
"""

import sys
import os
import json
import math
import pickle
import argparse
import heapq
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

try:
    from PIL import Image
    from PIL.ExifTags import GPSTAGS
except ImportError:
    print("Missing dependency: pip install pillow")
    sys.exit(1)

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    print("Warning: pillow-heif not installed, HEIC files may be skipped")

# gps and geometry

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".heic", ".webp"}

# convert to spherical coords, this makes the math easier or something
def lat_lon_to_xyz(lat, lon):
    rlat = math.radians(lat)
    rlon = math.radians(lon)
    x = math.cos(rlat) * math.cos(rlon)
    y = math.cos(rlat) * math.sin(rlon)
    z = math.sin(rlat)
    return x, y, z

# distance between two pts
def great_circle_km(a, b):
    dot = a["x"]*b["x"] + a["y"]*b["y"] + a["z"]*b["z"]
    dot = max(-1.0, min(1.0, dot))
    return 6371.0 * math.acos(dot)

# distance squared for tree compatibility
def euclidean_sq(a, b):
    dx = a["x"] - b["x"]
    dy = a["y"] - b["y"]
    dz = a["z"] - b["z"]
    return dx*dx + dy*dy + dz*dz

def make_photo(path, lat, lon):
    x, y, z = lat_lon_to_xyz(lat, lon)
    return {"path": str(path), "lat": lat, "lon": lon, "x": x, "y": y, "z": z}

# get coords from exif (evil, disable exif on your phone)

# typical metadata dirs, usually no useful info - comment out if stuff is broken
SKIP_DIRS = {"thumbs", "encoded-video", "profile"}

# file to disable dirs (useful for something like anti-vehicle)
NOINDEX_MARKER = ".noindex"

# chatgpt bs that works
def extract_gps(filepath):
    try:
        with Image.open(filepath) as img:
            # img.getexif() is the modern format-agnostic API — works for
            # JPEG, PNG, TIFF, and HEIC (via pillow-heif).
            # _getexif() is JPEG-only and returns None for HEIC files.
            exif = img.getexif()
            if not exif:
                return None

            # GPS data lives in a sub-IFD; getexif() doesn't include it
            # directly — need to pull it via get_ifd(0x8825).
            GPS_IFD_TAG = 0x8825
            gps_raw = exif.get_ifd(GPS_IFD_TAG)
            if not gps_raw:
                return None

            gps = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}

            def to_decimal(values, ref):
                deg, mn, sec = values
                deg = float(deg)
                mn  = float(mn)
                sec = float(sec)
                result = deg + mn / 60.0 + sec / 3600.0
                if ref in ("S", "W"):
                    result = -result
                return result

            if "GPSLatitude" not in gps or "GPSLongitude" not in gps:
                return None

            lat = to_decimal(gps["GPSLatitude"],  gps.get("GPSLatitudeRef",  "N"))
            lon = to_decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))

            if lat == 0.0 and lon == 0.0:
                return None

            return lat, lon

    except Exception:
        return None

# database tree utils (k-d tree)

def _coord(photo, axis):
    return photo[["x", "y", "z"][axis]]

def _build(photos, depth=0):
    if not photos:
        return None
    axis = depth % 3
    photos.sort(key=lambda p: _coord(p, axis))
    mid = len(photos) // 2
    return {
        "photo": photos[mid],
        "axis":  axis,
        "left":  _build(photos[:mid],    depth + 1),
        "right": _build(photos[mid+1:],  depth + 1),
    }

def _collect_photos(node):
    if node is None:
        return []
    return [*_collect_photos(node.get("left")), node["photo"], *_collect_photos(node.get("right"))]

# used for combining multiple index files
def combine_indexes(index_paths, out_path):
    photos = []

    for index_path in index_paths:
        with open(index_path, "rb") as f:
            index = pickle.load(f)
        photos.extend(_collect_photos(index.get("tree")))

    if not photos:
        raise ValueError("No photos found in the provided indexes.")

    tree = _build(photos)
    combined_index = {
        "tree": tree,
        "count": len(photos),
        "built": datetime.now().isoformat(),
    }

    with open(out_path, "wb") as f:
        pickle.dump(combined_index, f)

    return combined_index

def cmd_combine(args):
    combine_indexes(args.indexes, args.out)
    print(f"Combined index saved to {args.out}")

def _search(node, query, best):
    if node is None:
        return best

    photo = node["photo"]
    dx = query["x"] - photo["x"]
    dy = query["y"] - photo["y"]
    dz = query["z"] - photo["z"]
    dist_sq = dx*dx + dy*dy + dz*dz

    if dist_sq < best["dist_sq"]:
        best = {"dist_sq": dist_sq, "photo": photo}

    axis       = node["axis"]
    q_coord    = _coord(query, axis)
    n_coord    = _coord(photo, axis)
    first,  second = (node["left"], node["right"]) if q_coord < n_coord \
                  else (node["right"], node["left"])

    best = _search(first, query, best)

    if (q_coord - n_coord) ** 2 < best["dist_sq"]:
        best = _search(second, query, best)

    return best

# commands:
# index:

# limits max time spend on file (prevents invalid type, corrupted, *very* large photos, etc)
FILE_TIMEOUT = 5
# max concurrent heic processes, prevents resource overutilization ideally, change if your computer stops working
HEIC_PROCESS_LIMIT = 2
_heic_slots = threading.Semaphore(HEIC_PROCESS_LIMIT)

def _heic_worker(path_str, q):
    try:
        q.put(("ok", extract_gps(path_str)))
    except Exception as e:
        q.put(("err", str(e)))

def _process_file(fpath):
    if Path(fpath).suffix.lower() != ".heic":
        return fpath, extract_gps(fpath), None

    import multiprocessing
    with _heic_slots:
        result_q = multiprocessing.Queue()
        p = multiprocessing.Process(target=_heic_worker, args=(str(fpath), result_q))
        p.start()
        p.join(timeout=FILE_TIMEOUT)

        if p.is_alive():
            p.kill()
            p.join()
            return fpath, None, "timeout"

        if result_q.empty():
            return fpath, None, "no result"

        status, val = result_q.get_nowait()
        if status == "err":
            return fpath, None, val
        return fpath, val, None


def cmd_index(args):
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"Error: {directory} is not a directory.")
        sys.exit(1)

    verbose   = args.verbose
    workers   = args.workers
    skip_dirs = set(args.skip_dirs)
    global FILE_TIMEOUT
    FILE_TIMEOUT = args.timeout
    print(f"Scanning {directory} with {workers} threads ...")

    # assemble files
    all_files = []
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        # prune skip dirs in-place so os.walk won't descend into them
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        # if dir is marked as unindexed, skip the entire subtree.
        if NOINDEX_MARKER in files:
            dirs[:] = []
            continue

        for fname in sorted(files):
            if fname == NOINDEX_MARKER:
                continue
            fpath = Path(root) / fname
            if fpath.suffix.lower() in PHOTO_EXTENSIONS:
                all_files.append(fpath)

    total   = len(all_files)
    print(f"Found {total} photo files. Reading EXIF data...")

    photos = []
    skipped = 0
    checked = 0
    lock = threading.Lock()  # protect shared counters/list

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_file, f): f for f in all_files}

        for future in as_completed(futures):
            fpath, gps, err = future.result()
            with lock:
                checked += 1
                if gps is not None:
                    photos.append(make_photo(fpath, *gps))
                    if verbose:
                        print(f"  OK      {fpath}  ({gps[0]:.5f}, {gps[1]:.5f})")
                elif err == "timeout":
                    skipped += 1
                    if verbose:
                        print(f"  TIMEOUT {fpath}")
                else:
                    skipped += 1
                    if verbose:
                        print(f"  SKIP    {fpath}  (no GPS)")

                if not verbose and checked % 100 == 0:
                    print(f"  ... {checked}/{total} checked, "
                          f"{len(photos)} indexed so far")

    print(f"\nDone. {checked} photos checked — "
          f"{len(photos)} indexed, {skipped} skipped (no GPS data).")

    if not photos:
        print("No photos with GPS data found. Index not written.")
        sys.exit(1)

    print("Building tree...")
    tree = _build(photos)

    index = {"tree": tree, "count": len(photos), "built": datetime.now().isoformat()}
    with open(args.out, "wb") as f:
        pickle.dump(index, f)

    print(f"Index saved to {args.out}")

# search:

def cmd_search(args):
    if not Path(args.index).exists():
        print(f"Index not found: {args.index}")
        print("Run `python photofind.py index <directory>` first.")
        sys.exit(1)

    with open(args.index, "rb") as f:
        index = pickle.load(f)

    query = make_photo("query", args.lat, args.lon)
    all_photos = _collect_photos(index["tree"])
    if not all_photos:
        print("Index is empty. Rebuild the index and try again.")
        sys.exit(1)

    n = max(1, args.n)
    n = min(n, len(all_photos))

    closest = heapq.nsmallest(
        n,
        all_photos,
        key=lambda p: euclidean_sq(query, p),
    )

    results = []
    for rank, photo in enumerate(closest, start=1):
        dist_km = great_circle_km(query, photo)
        results.append({
            "rank": rank,
            "lat": photo["lat"],
            "lon": photo["lon"],
            "file": photo["path"],
            "distance_km": dist_km,
        })

    nearest = results[0]

    print(f"Top {n} closest photo(s):")
    for item in results:
        print(f"  #{item['rank']}: {item['file']}")
        print(f"      Location: {item['lat']:.6f}, {item['lon']:.6f}")
        print(f"      Distance: {item['distance_km']:.2f} km")

    output = {
        "query": {"lat": args.lat, "lon": args.lon},
        "nearest": {"lat": nearest["lat"], "lon": nearest["lon"], "file": nearest["file"]},
        "distance_km": nearest["distance_km"],
        "results": results,
        "count": len(results),
    }
    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results written to {args.out}")

    # result output graph using plotly
    try:
        import plotly.graph_objects as go
        html_path = str(Path(args.out).with_suffix(".html"))
        _show_map(output, html_path, go)
    except ImportError:
        print("Tip: pip install plotly  to also save an interactive map")

def _show_map(data, html_path, go):
    q = data["query"]
    nearest = data["nearest"]
    results = data.get("results") or [
        {
            "rank": 1,
            "lat": nearest["lat"],
            "lon": nearest["lon"],
            "file": nearest["file"],
            "distance_km": data["distance_km"],
        }
    ]

    fig = go.Figure()

    fig.add_trace(go.Scattermap(
        lat=[q["lat"], nearest["lat"]],
        lon=[q["lon"], nearest["lon"]],
        mode="lines",
        line=dict(color="#e74c3c", width=3),
        hoverinfo="skip",
        showlegend=False,
    ))

    fig.add_trace(go.Scattermap(
        lat=[item["lat"] for item in results],
        lon=[item["lon"] for item in results],
        mode="markers+text",
        marker=dict(size=12, color="royalblue", allowoverlap=True),
        text=[f"#{item['rank']}" for item in results],
        textposition="top right",
        hovertext=[
            f"Rank #{item['rank']}<br>Distance: {item['distance_km']:.2f} km"
            f"<br>File: {item['file']}"
            for item in results
        ],
        hoverinfo="text",
        name="Closest Photos",
    ))

    fig.add_trace(go.Scattermap(
        lat=[q["lat"]],
        lon=[q["lon"]],
        mode="markers+text",
        marker=dict(size=16, color="red", symbol="star", allowoverlap=True),
        text=["Target"],
        textposition="top right",
        hovertext=["Target"],
        hoverinfo="text",
        name="Target",
    ))

    fig.update_layout(
        map=dict(
            style="carto-positron",
            center=dict(lat=q["lat"], lon=q["lon"]),
            zoom=11,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        title="Nearest Photo to Target",
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
    )

    fig.write_html(html_path)
    print(f"Map saved to {html_path}")

# main

def main():
    parser = argparse.ArgumentParser(prog="photofind",
        description="Index and search photos by GPS location")
    sub = parser.add_subparsers(dest="command")

    p_index = sub.add_parser("index", help="Build index from a photo directory")
    p_index.add_argument("directory")
    p_index.add_argument("--out", default="index.pkl", metavar="FILE")
    p_index.add_argument("--verbose", "-v", action="store_true",
                         help="Print each file as it is scanned")
    p_index.add_argument("--workers", "-w", type=int,
                         default=min(8, max(2, os.cpu_count() or 4)),
                         metavar="N",
                         help="Number of indexing threads (default: up to 8)")
    p_index.add_argument("--timeout", type=int, default=FILE_TIMEOUT,
                         metavar="SECS",
                         help="Per-file timeout in seconds (default: 5)")
    p_index.add_argument("--skip-dirs", nargs="*", default=list(SKIP_DIRS),
                         metavar="DIR",
                         help="Directory names to skip (default: thumbs encoded-video profile)")

    p_search = sub.add_parser("search", help="Find nearest photo to a coordinate")
    p_search.add_argument("lat",  type=float)
    p_search.add_argument("lon",  type=float)
    p_search.add_argument("--index", default="index.pkl", metavar="FILE")
    p_search.add_argument("--out",   default="results.json", metavar="FILE")
    p_search.add_argument("--n", "-n", type=int, default=1, metavar="N",
                          help="Number of closest photos to return (default: 1)")

    p_combine = sub.add_parser("combine", help="Merge multiple indexes into one")
    p_combine.add_argument("indexes", nargs="+", metavar="FILE",
                           help="Input index .pkl files to merge")
    p_combine.add_argument("--out", default="merged_index.pkl", metavar="FILE",
                           help="Output merged index file")

    args = parser.parse_args()

    if args.command == "index":
        cmd_index(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "combine":
        cmd_combine(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
