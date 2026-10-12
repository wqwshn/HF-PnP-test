"""Local, single-user Fig. 1g/h editor. Source CSVs are never modified."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, FormatStrFormatter
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULTS = dict(direction="DOWN", channel="Ut1", area=math.pi * 19**2 / 4,
                diameter=19, mode="pressure", zero=0, window=1,
                width=38.1, height=33.16, fontsize=7, linewidth=0.8,
                color="#D986A2", sd=True, ymin_g=None, ymax_g=None,
                ymin_h=None, ymax_h=None, selected=0, overrides={})
SESSIONS = {}
PLOT_LOCK = threading.Lock()
plt.rcParams.update({"font.family": "Arial", "svg.fonttype": "none",
                     "pdf.fonttype": 42, "axes.linewidth": .6,
                     "axes.unicode_minus": True})


def read_rows(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        raise ValueError("CSV 没有数据行。")
    return [{k.strip().replace("（", "(").replace("）", ")"): v
             for k, v in r.items() if k is not None} for r in rows]


def number(value, label, missing=False):
    if value is None or str(value).strip() == "":
        if missing:
            return None
        raise ValueError(f"{label} 不能为空。")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} 必须是数字。") from None
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须是有限数字。")
    return result


def parse_dataset(raw_text, marker_text, raw_name, marker_name):
    # Native text reads and browser uploads may differ only in BOM / newlines.
    raw_text = raw_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    marker_text = marker_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    raw, markers = read_rows(raw_text), read_rows(marker_text)
    required = {"Time(s)", "SampleIndex", "Ut1(mV)", "Ut2(mV)"}
    if not required <= raw[0].keys():
        raise ValueError("原始 CSV 缺少 Time(s)、SampleIndex 或 Ut1/Ut2(mV)。")
    t = np.array([number(r["Time(s)"], "时间") for r in raw])
    index = np.array([number(r["SampleIndex"], "SampleIndex") for r in raw])
    if np.any(np.diff(t) <= 0) or np.any(np.diff(index) <= 0) or np.any(index != np.floor(index)):
        raise ValueError("时间和 SampleIndex 必须严格递增，SampleIndex 必须为整数。")
    channels = {}
    for channel in ("Ut1", "Ut2"):
        values = []
        for row in raw:
            try:
                values.append(float(row[channel + "(mV)"]) / 1000)
            except (ValueError, TypeError):
                values.append(float("nan"))
        channels[channel] = np.array(values)
    valid = np.array([r.get("ValidFlag", "1") in ("1", "1.0") and
                      r.get("InterpFlag", "0") in ("0", "0.0") for r in raw])
    lookup = {int(v): i for i, v in enumerate(index)}
    parsed = []
    seen = set()
    for i, m in enumerate(markers):
        sample = number(m.get("SampleIndex"), f"marker {i+1} SampleIndex")
        if sample != int(sample) or int(sample) not in lookup:
            raise ValueError(f"marker {i+1} 的 SampleIndex 在原始文件中不存在。")
        marker_id = m.get("MarkerNo", str(i+1)).strip()
        if marker_id in seen:
            raise ValueError("MarkerNo 必须唯一。")
        seen.add(marker_id)
        parsed.append(dict(marker=marker_id, sample=int(sample), row=lookup[int(sample)],
                           distance=number(m.get("Note-distance(mm)"), "距离", True),
                           force=number(m.get("Note-force(N)"), "力", True)))
    return dict(name=Path(raw_name).name, marker_name=Path(marker_name).name,
                t=t, index=index, valid=valid, channels=channels, markers=parsed,
                raw_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                marker_sha256=hashlib.sha256(marker_text.encode("utf-8")).hexdigest())


def normalize_config(input_config):
    c = {**DEFAULTS, **input_config}
    if c["channel"] not in ("Ut1", "Ut2") or c["direction"] not in ("DOWN", "UP"):
        raise ValueError("通道或采集方向无效。")
    if c["mode"] not in ("force", "pressure"):
        raise ValueError("h 横轴模式无效。")
    for key, low, high in [("window", .01, 60), ("width", 30, 150),
                           ("height", 30, 150), ("fontsize", 6, 16),
                           ("linewidth", .1, 4)]:
        c[key] = number(c[key], key)
        if not low <= c[key] <= high:
            raise ValueError(f"{key} 应在 {low}–{high} 之间。")
    c["zero"] = number(c["zero"], "接触原点")
    c["area"] = number(c["area"], "有效受压面积", True)
    if c["area"] is not None and c["area"] <= 0:
        raise ValueError("有效受压面积必须大于零。")
    if c["mode"] == "pressure" and c["area"] is None:
        raise ValueError("请填写面积，或将 h 切换为接触力。")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", c["color"]):
        raise ValueError("曲线颜色格式无效。")
    for panel in ("g", "h"):
        for key in (f"ymin_{panel}", f"ymax_{panel}"):
            c[key] = number(c[key], key, True)
        lo, hi = c[f"ymin_{panel}"], c[f"ymax_{panel}"]
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError(f"{panel} 的纵轴上限必须大于下限。")
    if not isinstance(c["overrides"], dict):
        raise ValueError("逐点窗口设置格式无效。")
    return c


def summarize(ds, config, notes=None):
    c = normalize_config(config)
    if notes is None:
        notes = [[m["distance"], m["force"]] for m in ds["markers"]]
    if len(notes) != len(ds["markers"]):
        raise ValueError(f"粘贴行数 {len(notes)} 与 marker 数 {len(ds['markers'])} 不一致。请保留空力所在行。")
    points, warnings = [], []
    t, values = ds["t"], ds["channels"][c["channel"]]
    for m, note in zip(ds["markers"], notes):
        if len(note) != 2:
            raise ValueError("每个 marker 必须对应距离、力两列。")
        distance = number(note[0], f"marker {m['marker']} 距离", True)
        force = number(note[1], f"marker {m['marker']} 力", True)
        if force is not None and force < 0:
            raise ValueError(f"marker {m['marker']} 力小于 0，请检查单位和输入。")
        window = number(c["overrides"].get(m["marker"], c["window"]), "逐点窗口")
        if not .01 <= window <= 60:
            raise ValueError("逐点窗口应在 0.01–60 s 之间。")
        end = float(t[m["row"]])
        start = end - window
        # Half-open interval: exactly the preceding second, excluding the click sample.
        a, b = np.searchsorted(t, [start - 1e-9, end - 1e-9])
        sample = values[a:b]
        usable = ds["valid"][a:b] & np.isfinite(sample)
        sample = sample[usable]
        n = len(sample)
        mean = float(np.mean(sample)) if n else None
        sd = float(np.std(sample, ddof=1)) if n >= 2 else None
        if start < t[0] or n < b-a or n < 2:
            warnings.append(f"marker {m['marker']}：窗口边界或无效样本，实际有效 n={n}。")
        points.append(dict(marker=m["marker"], sample=m["sample"], time=end,
                           distance_raw=distance,
                           distance=None if distance is None else distance-c["zero"],
                           force=force, pressure=None if force is None or c["area"] is None else 1000*force/c["area"],
                           mean=mean, sd=sd, n=n, window=window,
                           window_start=start, window_end=end))
    if sum(p["distance"] is not None and p["mean"] is not None for p in points) < 2:
        warnings.append("g 有效点少于 2 个，不能连接或插值。")
    if sum(p["force"] is not None and p["mean"] is not None for p in points) < 2:
        warnings.append("h 有效点少于 2 个，不能连接或插值。")
    if abs(c["zero"]) > 0:
        warnings.append(f"手动距离校正：d = d_raw − ({c['zero']:g} mm)，不是仪器测得的接触时刻。")
    return c, points, warnings


def series(points, key, lo, hi, extra=()):
    """Piecewise linear segments in acquisition order; no extrapolation or averaging duplicates."""
    pairs = [(p[key], p["mean"]) for p in points if p[key] is not None and p["mean"] is not None]
    segments, interpolated = [], []
    targets = sorted(set([lo, hi, *extra]))
    for (x1, y1), (x2, y2) in zip(pairs[:-1], pairs[1:]):
        if x1 == x2:
            if lo <= x1 <= hi:
                segments.append(([x1, x2], [y1, y2]))
            continue
        knots = [(x1, y1), (x2, y2)]
        for x in targets:
            if min(x1, x2) < x < max(x1, x2):
                y = y1+(y2-y1)*(x-x1)/(x2-x1)
                knots.append((x, y))
                interpolated.append(dict(x=float(x), mean=float(y), kind="linear_interpolation"))
        knots.sort(key=lambda p: p[0], reverse=x2 < x1)
        visible = [(x, y) for x, y in knots if lo <= x <= hi]
        if len(visible) >= 2:
            segments.append(tuple(zip(*visible)))
    return segments, interpolated


def explicit_svg(data):
    """Expand Matplotlib's font shorthand for editable Illustrator text."""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    root = ET.fromstring(data)
    for el in root.iter():
        style = el.get("style", "")
        rules = []
        for token in style.split(";"):
            if ":" not in token:
                continue
            key, value = [v.strip() for v in token.split(":", 1)]
            if key == "font":
                match = re.search(r"([\d.]+)px\s+(.+)$", value)
                if match:
                    el.set("font-size", match.group(1))
                    el.set("font-family", match.group(2).replace("'", ""))
                    el.set("font-weight", "700" if "bold" in value else "400")
                else:
                    rules.append(token)
            else:
                rules.append(token)
        if style:
            el.set("style", "; ".join(rules))
    return ET.tostring(root, encoding="unicode")


def encode_figure(fig, fmt="svg", dpi=150):
    buffer = io.BytesIO()
    fig.savefig(buffer, format=fmt, dpi=dpi, facecolor="white")
    result = buffer.getvalue()
    return explicit_svg(result) if fmt == "svg" else result


def make_panel(points, c, panel):
    fig, ax = plt.subplots(figsize=(c["width"]/25.4, c["height"]/25.4))
    # Shared physical margins give the two panels equal plot rectangles.
    fig.subplots_adjust(left=11/c["width"], right=1-2/c["width"],
                        bottom=9/c["height"], top=1-2/c["height"])
    if panel == "g":
        key, lo, hi, label = "distance", -1, 2, "Distance (mm)"
        ax.axvline(0, color="#949BA5", linewidth=.55, linestyle=(0, (3, 3)), zorder=0)
        ax.set_xlim(hi, lo)
        ax.set_xticks([2, 1, 0, -1])
    else:
        key = "pressure" if c["mode"] == "pressure" else "force"
        lo, hi = 0, 5000/c["area"] if key == "pressure" else 5
        label = "Contact pressure (kPa)" if key == "pressure" else "Contact force (N)"
        ax.set_xlim(lo, hi)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4, min_n_ticks=3, steps=[1, 2, 2.5, 5, 10]))
    segments, interp = series(points, key, lo, hi, [0] if panel == "g" else [])
    for x, y in segments:
        ax.plot(x, y, color=c["color"], lw=c["linewidth"], zorder=2)
    visible = [p for p in points if p[key] is not None and p["mean"] is not None and lo <= p[key] <= hi]
    if visible:
        ax.plot([p[key] for p in visible], [p["mean"] for p in visible], "o",
                color=c["color"], ms=2.3, mew=.4, mfc="white", zorder=3)
        if c["sd"]:
            error = [p for p in visible if p["sd"] is not None]
            ax.errorbar([p[key] for p in error], [p["mean"] for p in error],
                        yerr=[p["sd"] for p in error], fmt="none", color=c["color"],
                        elinewidth=.5, capsize=1.4, capthick=.5, zorder=1)
    ax.set_xlabel(label, fontsize=c["fontsize"], labelpad=2)
    ax.set_ylabel(f"{c['channel']} (V)", fontsize=c["fontsize"], labelpad=2)
    ax.tick_params(labelsize=c["fontsize"], width=.6, length=2, pad=2)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=3))
    ax.yaxis.set_major_formatter(FormatStrFormatter("%.2f"))
    ax.spines[["top", "right"]].set_visible(False)
    ax.margins(y=.15)
    ax.set_ylim(bottom=c[f"ymin_{panel}"], top=c[f"ymax_{panel}"])
    fig.canvas.draw()
    # Return bounds and audit actual text extents, not only source fontsize.
    renderer = fig.canvas.get_renderer()
    frame = fig.bbox
    clipping, text_boxes = [], []
    for artist in [ax.xaxis.label, ax.yaxis.label, *ax.get_xticklabels(), *ax.get_yticklabels()]:
        if not artist.get_visible() or not artist.get_text():
            continue
        if artist in ax.get_xticklabels() and not lo <= artist.get_position()[0] <= hi:
            continue
        if artist in ax.get_yticklabels() and not ax.get_ylim()[0] <= artist.get_position()[1] <= ax.get_ylim()[1]:
            continue
        bb = artist.get_window_extent(renderer)
        text_boxes.append((artist.get_text(), bb))
        if bb.x0 < -1 or bb.y0 < -1 or bb.x1 > frame.width+1 or bb.y1 > frame.height+1:
            clipping.append(artist.get_text())
    overlaps = []
    for i, (name, box) in enumerate(text_boxes):
        for other_name, other in text_boxes[i+1:]:
            if min(box.x1, other.x1)-max(box.x0, other.x0) > 1 and min(box.y1, other.y1)-max(box.y0, other.y0) > 1:
                overlaps.append(f"{name} / {other_name}")
    meta = dict(visible_measured_points=len(visible), interpolation=interp,
                axes_bounds=list(ax.get_position().bounds), clipping=clipping, overlaps=overlaps,
                x_range=[hi, lo] if panel == "g" else [lo, hi], y_range=list(ax.get_ylim()))
    return fig, meta


def diagnostic_figures(ds, points, c):
    fig, ax = plt.subplots(figsize=(8.5, 2.25))
    fig.subplots_adjust(left=.085, right=.98, bottom=.25, top=.92)
    p = [p for p in points if p["distance_raw"] is not None and p["mean"] is not None]
    if p:
        ax.plot([a["distance_raw"] for a in p], [a["mean"] for a in p], "o-", color=c["color"], ms=3, lw=1)
        low, high = min(a["distance_raw"] for a in p), max(a["distance_raw"] for a in p)
        pad = max((high-low)*.04, .1)
        ax.set_xlim(high+pad, low-pad)
    ax.axvline(c["zero"], color="#527F89", ls="--", lw=1)
    ax.set_xlabel("Recorded distance (mm)", fontsize=9)
    ax.set_ylabel(f"{c['channel']} (V)", fontsize=9)
    ax.tick_params(labelsize=8)
    ax.spines[["right", "top"]].set_visible(False)
    bounds = list(ax.get_position().bounds)
    overview = dict(svg=encode_figure(fig), bounds=bounds, xlim=list(ax.get_xlim()))
    plt.close(fig)
    selected = max(0, min(int(c["selected"]), len(points)-1))
    point = points[selected]
    fig, ax = plt.subplots(figsize=(8.5, 2.35))
    fig.subplots_adjust(left=.085, right=.98, bottom=.25, top=.92)
    t, values = ds["t"], ds["channels"][c["channel"]]
    end, start = point["window_end"], point["window_start"]
    a, b = np.searchsorted(t, [start-2, end+2])
    signal = values[a:b].copy()
    signal[~ds["valid"][a:b]] = np.nan
    ax.plot(t[a:b], signal, color="#61718A", lw=.7)
    ax.axvspan(start, end, color="#BFDAD8", alpha=.6)
    ax.axvline(end, color="#C77F91", lw=.9, ls="--")
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel(f"{c['channel']} (V)", fontsize=9)
    ax.tick_params(labelsize=8)
    ax.spines[["right", "top"]].set_visible(False)
    trace = encode_figure(fig)
    plt.close(fig)
    return overview, trace


def manifest(ds, c, notes):
    return dict(version=1, source=ds["name"], marker_source=ds["marker_name"],
                raw_sha256=ds["raw_sha256"], marker_sha256=ds["marker_sha256"],
                hash_encoding="UTF-8 text, BOM removed, LF line endings",
                config=c, notes=notes, timestamp=datetime.now().isoformat(timespec="seconds"),
                window_definition="[marker_time - window_seconds, marker_time)",
                statistics="mean and sample SD (ddof=1) of valid, non-interpolated finite voltage samples; mV / 1000",
                interpolation="piecewise linear in acquisition order; measured points retained; no extrapolation",
                area_definition="user-supplied effective load-bearing contact area in mm^2; default circular 19 mm head",
                pressure_formula="pressure_kPa = 1000 * force_N / area_mm2")


def render(ds, payload, export=False):
    c, points, warnings = summarize(ds, payload.get("config", {}), payload.get("notes"))
    notes = [[p["distance_raw"], p["force"]] for p in points]
    result = dict(config=c, points=points, warnings=warnings, panels={})
    with PLOT_LOCK:
        figures = []
        try:
            for panel in ("g", "h"):
                fig, meta = make_panel(points, c, panel)
                figures.append(fig)
                result["panels"][panel] = dict(svg=encode_figure(fig), **meta)
                if meta["clipping"]:
                    warnings.append(f"{panel} 文字可能超出画布：{', '.join(meta['clipping'])}。请增大画布或减小字号。")
                if meta["overlaps"]:
                    warnings.append(f"{panel} 文字重叠：{', '.join(meta['overlaps'])}。请增大画布或减小字号。")
            if export:
                if any(p["clipping"] or p["overlaps"] for p in result["panels"].values()):
                    raise ValueError("检测到文字超出画布或相互重叠，调整宽高或字号后再导出。")
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                stem = re.sub(r"[^\w.-]", "_", Path(ds["name"]).stem)
                prefix = f"{stem}_{c['direction']}_{c['channel']}"
                folder = ROOT / "exports" / f"{prefix}_{stamp}"
                folder.mkdir(parents=True)
                files = []
                for panel, fig in zip(("g", "h"), figures):
                    base = f"{prefix}_{panel}_{stamp}"
                    for fmt in ("svg", "png", "pdf"):
                        target = folder / f"{base}.{fmt}"
                        content = result["panels"][panel]["svg"] if fmt == "svg" else encode_figure(fig, fmt, 600)
                        target.write_text(content, encoding="utf-8") if isinstance(content, str) else target.write_bytes(content)
                        files.append(target.name)
                settings = manifest(ds, c, notes)
                (folder / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
                with (folder / "points.csv").open("w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=list(points[0]))
                    writer.writeheader()
                    writer.writerows(points)
                (folder / "figure_metadata.json").write_text(json.dumps({p: {k: v for k, v in result['panels'][p].items() if k != 'svg'} for p in ('g', 'h')}, indent=2), encoding="utf-8")
                files += ["settings.json", "points.csv", "figure_metadata.json"]
                result["export"] = dict(folder=str(folder), files=[dict(name=f, url="/exports/"+folder.name+"/"+f) for f in files])
            else:
                result["overview"], result["trace"] = diagnostic_figures(ds, points, c)
        finally:
            for fig in figures:
                plt.close(fig)
    return result


def register(raw, marker, raw_name, marker_name):
    ds = parse_dataset(raw, marker, raw_name, marker_name)
    sid = uuid.uuid4().hex
    SESSIONS[sid] = ds
    # This local tool keeps only a small number of datasets in memory.
    while len(SESSIONS) > 6:
        del SESSIONS[next(iter(SESSIONS))]
    return dict(session=sid, name=ds["name"], marker_name=ds["marker_name"],
                samples=len(ds["t"]), duration=float(ds["t"][-1]-ds["t"][0]),
                raw_sha256=ds["raw_sha256"], marker_sha256=ds["marker_sha256"],
                markers=[{k: v for k, v in m.items() if k != "row"} for m in ds["markers"]],
                defaults=DEFAULTS)


def choose_file(kind):
    command = [sys.executable, str(ROOT / "app.py"), "--choose", kind]
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", **kwargs)
    if result.returncode:
        raise ValueError("文件选择器未能启动；可使用浏览器文件上传入口。")
    return json.loads(result.stdout.strip() or '""')


class Handler(BaseHTTPRequestHandler):
    def json_response(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import unquote, urlsplit
        path = unquote(urlsplit(self.path).path)
        if path == "/api/defaults":
            return self.json_response(DEFAULTS)
        if path == "/":
            file = ROOT / "index.html"
        elif path.startswith("/exports/"):
            file = (ROOT / path.lstrip("/")).resolve()
            if ROOT / "exports" not in file.parents:
                return self.send_error(403)
        else:
            return self.send_error(404)
        if not file.is_file():
            return self.send_error(404)
        import mimetypes
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            from urllib.parse import urlsplit
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.json_response({"error": "不接受来自其他网站的操作。"}, 403)
            size = int(self.headers.get("Content-Length", "0"))
            if size > 80_000_000:
                raise ValueError("单次导入请小于 80 MB。")
            p = json.loads(self.rfile.read(size) or b"{}")
            if self.path == "/api/demo":
                file = ROOT / "data" / "demo_DOWN.csv"
                marker = file.with_name(file.stem+"_markers.csv")
                result = register(file.read_text(encoding="utf-8-sig"), marker.read_text(encoding="utf-8-sig"), file.name, marker.name)
            elif self.path == "/api/choose":
                raw_path = choose_file("raw")
                if not raw_path:
                    return self.json_response({"cancelled": True})
                file = Path(raw_path)
                marker = file.with_name(file.stem+"_markers.csv")
                if not marker.exists():
                    chosen = choose_file("marker")
                    if not chosen:
                        return self.json_response({"cancelled": True})
                    marker = Path(chosen)
                result = register(file.read_text(encoding="utf-8-sig"), marker.read_text(encoding="utf-8-sig"), file.name, marker.name)
            elif self.path == "/api/upload":
                result = register(p["raw"], p["marker"], p["raw_name"], p["marker_name"])
            elif self.path in ("/api/preview", "/api/export"):
                ds = SESSIONS.get(p.get("session"))
                if ds is None:
                    raise ValueError("请重新导入原始文件和 marker 文件。")
                result = render(ds, p, export=self.path.endswith("export"))
            else:
                return self.send_error(404)
            self.json_response(result)
        except (ValueError, KeyError, OSError) as e:
            self.json_response({"error": str(e)}, 400)
        except Exception:
            traceback.print_exc()
            self.json_response({"error": "处理失败，请查看本地 server-error.log。"}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--choose", choices=["raw", "marker"])
    args = parser.parse_args()
    if args.choose:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(parent=root, title="选择原始 CSV" if args.choose == "raw" else "选择配套 marker CSV", initialdir=ROOT / "data", filetypes=[("CSV", "*.csv")])
        root.destroy()
        print(json.dumps(path, ensure_ascii=True))
        return
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Fig1g/h: http://127.0.0.1:{args.port}", flush=True)
    if args.open_browser:
        threading.Timer(.7, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
