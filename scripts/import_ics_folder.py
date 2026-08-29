#!/usr/bin/env python3
"""
将指定文件夹下所有 .ics 文件中的事件导入到 vdir/（seminars 日历）。

每个 VEVENT 单独存为一个 .ics 文件，文件名由 UID 的 SHA1 决定，
重复导入同一数据会覆盖原文件，不会产生重复。

用法：
    python3 scripts/import_ics_folder.py <文件夹路径>
    python3 scripts/import_ics_folder.py .   # 当前目录下的所有 .ics 文件
"""

import hashlib
import sys
from pathlib import Path

from icalendar import Calendar, Event

VDIR = Path(__file__).parent.parent / "vdir"

VCAL_HEADER = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "PRODID:-//upcoming_event_viewing//EN\r\n"
)
VCAL_FOOTER = "END:VCALENDAR\r\n"


def uid_to_filename(uid: str) -> str:
    return hashlib.sha1(uid.encode()).hexdigest()[:16] + ".ics"


def import_file(ics_path: Path) -> int:
    """导入单个 .ics 文件，返回写入的事件数。"""
    try:
        data = ics_path.read_bytes()
        cal = Calendar.from_ical(data)
    except Exception as e:
        print(f"  [跳过] {ics_path.name}: 解析失败 ({e})")
        return 0

    written = 0
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))
        if not uid:
            # 没有 UID 则用 SUMMARY+DTSTART 生成
            summary = str(component.get("SUMMARY", "unknown"))
            dtstart = str(component.get("DTSTART", ""))
            uid = hashlib.sha1(f"{summary}|{dtstart}".encode()).hexdigest() + "@imported"
            component.add("UID", uid)

        # 将单个 VEVENT 包装成完整 VCALENDAR
        out_cal = Calendar()
        out_cal.add("VERSION", "2.0")
        out_cal.add("PRODID", "-//upcoming_event_viewing//EN")
        out_cal.add_component(component)

        filename = uid_to_filename(uid)
        out_path = VDIR / filename
        out_path.write_bytes(out_cal.to_ical())

        summary = str(component.get("SUMMARY", "(无标题)"))
        dtstart = component.get("DTSTART")
        dt_str = str(dtstart.dt) if dtstart else "?"
        print(f"  写入 {filename}  {dt_str}  {summary}")
        written += 1

    return written


def main():
    if len(sys.argv) != 2:
        print(f"用法: {sys.argv[0]} <文件夹路径>")
        sys.exit(1)

    folder = Path(sys.argv[1]).resolve()
    if not folder.is_dir():
        print(f"错误：{folder} 不是有效目录")
        sys.exit(1)

    ics_files = [
        p for p in folder.iterdir()
        if p.suffix.lower() == ".ics" and not p.name.endswith(":Zone.Identifier")
    ]

    if not ics_files:
        print(f"在 {folder} 中未找到 .ics 文件")
        sys.exit(0)

    print(f"找到 {len(ics_files)} 个 .ics 文件，目标目录：{VDIR}\n")
    total = 0
    for f in sorted(ics_files):
        print(f"处理：{f.name}")
        total += import_file(f)

    print(f"\n完成：共导入 {total} 个事件到 {VDIR}/")


if __name__ == "__main__":
    main()
