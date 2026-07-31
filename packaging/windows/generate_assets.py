"""Generate deterministic ICO and Windows version resources."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_version() -> str:
    text = (PROJECT_ROOT / "src/ble_calibration/version.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'__version__ = "([^"]+)"', text)
    if match is None:
        raise RuntimeError("cannot find application version")
    return match.group(1)


def version_tuple(version: str) -> tuple:
    numeric = [int(part) for part in version.split(".")[:4]]
    return tuple((numeric + [0, 0, 0, 0])[:4])


def write_icon(path: Path) -> None:
    size = 256
    image = Image.new("RGBA", (size, size), (7, 17, 31, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (8, 8, size - 8, size - 8),
        radius=52,
        fill=(12, 31, 53, 255),
        outline=(69, 158, 255, 255),
        width=9,
    )
    draw.rounded_rectangle(
        (35, 47, size - 35, size - 47),
        radius=34,
        fill=(20, 57, 91, 255),
    )
    draw.arc((48, 60, 208, 220), 205, 335, fill=(86, 190, 255, 255), width=14)
    draw.arc((48, 36, 208, 196), 25, 155, fill=(86, 190, 255, 255), width=14)
    try:
        font = ImageFont.truetype("arialbd.ttf", 76)
    except OSError:
        font = ImageFont.load_default()
    label = "BLE"
    box = draw.textbbox((0, 0), label, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text(
        ((size - width) / 2, (size - height) / 2 - 8),
        label,
        font=font,
        fill=(239, 248, 255, 255),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def write_version_file(path: Path, version: str) -> None:
    values = version_tuple(version)
    version_csv = ", ".join(str(value) for value in values)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version_csv}),
    prodvers=({version_csv}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [
          StringStruct('CompanyName', 'BLE Calibration Team'),
          StringStruct('FileDescription', '汽车数字钥匙 BLE 标定工具'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'BLECalibration'),
          StringStruct('OriginalFilename', 'BLECalibration.exe'),
          StringStruct('ProductName', 'BLE Calibration'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "build/windows-assets",
    )
    args = parser.parse_args()
    version = project_version()
    write_icon(args.output_dir / "BLECalibration.ico")
    write_version_file(args.output_dir / "version_info.txt", version)
    print(f"Windows assets generated for {version}: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
