"""Small, bounded PNG checks for viewport-capture evidence."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_FILE_BYTES = 16_000_000
_MAX_ANALYZED_PIXELS = 1_200_000
_MAX_SAMPLED_PIXELS = 100_000


def analyze_png_quality(
    path: Path,
    *,
    expected_resolution: tuple[int, int] | None,
    require_exact_resolution: bool,
) -> dict[str, Any]:
    """Inspect an ordinary 8-bit PNG without an image-library dependency."""

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        return _unverified("image_read_failed", str(exc))
    if file_size > _MAX_FILE_BYTES:
        return _unverified(
            "analysis_input_too_large",
            f"PNG exceeds the {_MAX_FILE_BYTES}-byte analysis limit",
        )
    try:
        raw = path.read_bytes()
        image = _decode_png(raw)
    except (OSError, ValueError, zlib.error) as exc:
        return _unverified("png_pixel_analysis_unavailable", str(exc))

    width = image["width"]
    height = image["height"]
    pixel_count = width * height
    sampled_pixels = image["sampled_pixels"]
    reasons: list[dict[str, str]] = []
    failed = False
    warning = False

    if expected_resolution is not None:
        expected_width, expected_height = expected_resolution
        expected_aspect = expected_width / expected_height
        actual_aspect = width / height
        if abs(actual_aspect / expected_aspect - 1.0) > 0.03:
            failed = True
            reasons.append(
                {
                    "code": "aspect_ratio_mismatch",
                    "message": (
                        f"Captured aspect {width}:{height} does not match "
                        f"expected {expected_width}:{expected_height}"
                    ),
                }
            )
        elif require_exact_resolution and (width, height) != expected_resolution:
            failed = True
            reasons.append(
                {
                    "code": "resolution_mismatch",
                    "message": (
                        f"Captured resolution {width}x{height} does not match "
                        f"requested {expected_width}x{expected_height}"
                    ),
                }
            )

    mean_rgb = (
        image["channel_totals"][0] / sampled_pixels,
        image["channel_totals"][1] / sampled_pixels,
        image["channel_totals"][2] / sampled_pixels,
    )
    channel_ranges = [
        image["channel_maximums"][index] - image["channel_minimums"][index]
        for index in range(3)
    ]
    dark_fraction = image["dark_count"] / sampled_pixels
    bright_fraction = image["bright_count"] / sampled_pixels
    if dark_fraction >= 0.985:
        failed = True
        reasons.append(
            {
                "code": "near_all_black",
                "message": "At least 98.5% of analyzed pixels are near black",
            }
        )
    if bright_fraction >= 0.985:
        failed = True
        reasons.append(
            {
                "code": "severe_highlight_clipping",
                "message": "At least 98.5% of analyzed pixels are clipped near white",
            }
        )
    if (
        max(channel_ranges) <= 2
        and max(mean_rgb) - min(mean_rgb) <= 4
        and dark_fraction < 0.985
        and bright_fraction < 0.985
    ):
        failed = True
        reasons.append(
            {
                "code": "flat_no_content",
                "message": (
                    "The image is nearly uniform and contains no verifiable "
                    "visual detail"
                ),
            }
        )

    channel_names = ("red", "green", "blue")
    dominant_index = max(range(3), key=mean_rgb.__getitem__)
    other_means = [
        value for index, value in enumerate(mean_rgb) if index != dominant_index
    ]
    if (
        mean_rgb[dominant_index] >= 96
        and mean_rgb[dominant_index] - max(other_means) >= 80
        and image["dominant_counts"][dominant_index] / sampled_pixels >= 0.90
    ):
        warning = True
        reasons.append(
            {
                "code": "single_channel_cast",
                "message": (
                    "The image has an extreme "
                    f"{channel_names[dominant_index]}-channel cast"
                ),
            }
        )

    return {
        "status": "failed" if failed else ("warning" if warning else "passed"),
        "reasons": reasons,
        "metrics": {
            "analyzed_pixels": sampled_pixels,
            "total_pixels": pixel_count,
            "mean_rgb": [round(value, 3) for value in mean_rgb],
            "channel_ranges": channel_ranges,
            "near_black_fraction": round(dark_fraction, 6),
            "near_white_fraction": round(bright_fraction, 6),
            "png_bit_depth": image["bit_depth"],
            "png_color_type": image["color_type"],
        },
    }


def _unverified(code: str, message: str) -> dict[str, Any]:
    return {
        "status": "unverified",
        "reasons": [{"code": code, "message": str(message)[:512]}],
        "metrics": {},
    }


def _decode_png(raw: bytes) -> dict[str, Any]:
    if not raw.startswith(_PNG_SIGNATURE):
        raise ValueError("invalid PNG signature")
    position = len(_PNG_SIGNATURE)
    width = height = bit_depth = color_type = None
    interlace = None
    compressed = bytearray()
    saw_iend = False
    while position + 12 <= len(raw):
        length = struct.unpack(">I", raw[position : position + 4])[0]
        chunk_type = raw[position + 4 : position + 8]
        data_start = position + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(raw):
            raise ValueError("truncated PNG chunk")
        data = raw[data_start:data_end]
        position = crc_end
        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError("invalid PNG IHDR")
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", data)
            if compression != 0 or filtering != 0:
                raise ValueError("unsupported PNG compression or filter method")
        elif chunk_type == b"IDAT":
            compressed.extend(data)
            if len(compressed) > _MAX_FILE_BYTES:
                raise ValueError("PNG compressed data exceeds analysis limit")
        elif chunk_type == b"IEND":
            saw_iend = True
            break
    if (
        not saw_iend
        or width is None
        or height is None
        or bit_depth is None
        or color_type is None
    ):
        raise ValueError("incomplete PNG")
    if width <= 0 or height <= 0:
        raise ValueError("invalid PNG dimensions")
    if width * height > _MAX_ANALYZED_PIXELS:
        raise ValueError(
            f"PNG exceeds the {_MAX_ANALYZED_PIXELS}-pixel analysis limit"
        )
    if bit_depth != 8 or color_type not in {0, 2, 4, 6} or interlace != 0:
        raise ValueError(
            "pixel analysis supports non-interlaced 8-bit gray/RGB/RGBA PNGs"
        )

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    row_bytes = width * channels
    expected_bytes = (row_bytes + 1) * height
    decompressor = zlib.decompressobj()
    scanlines = decompressor.decompress(bytes(compressed), expected_bytes + 1)
    if len(scanlines) > expected_bytes or not decompressor.eof:
        raise ValueError("PNG decompressed data exceeds expected dimensions")
    if len(scanlines) != expected_bytes:
        raise ValueError("PNG scanline data is incomplete")

    previous = bytearray(row_bytes)
    sample_stride = max(
        1,
        (width * height + _MAX_SAMPLED_PIXELS - 1) // _MAX_SAMPLED_PIXELS,
    )
    sampled_pixels = 0
    channel_totals = [0, 0, 0]
    channel_minimums = [255, 255, 255]
    channel_maximums = [0, 0, 0]
    dark_count = bright_count = 0
    dominant_counts = [0, 0, 0]
    pixel_index = 0
    offset = 0
    for _row_index in range(height):
        filter_type = scanlines[offset]
        offset += 1
        current = bytearray(scanlines[offset : offset + row_bytes])
        offset += row_bytes
        _unfilter(current, previous, filter_type, channels)
        for pixel_offset in range(0, row_bytes, channels):
            if color_type == 0:
                red = green = blue = current[pixel_offset]
                alpha = 255
            elif color_type == 2:
                red, green, blue = current[pixel_offset : pixel_offset + 3]
                alpha = 255
            elif color_type == 4:
                red = green = blue = current[pixel_offset]
                alpha = current[pixel_offset + 1]
            else:
                red, green, blue, alpha = current[
                    pixel_offset : pixel_offset + 4
                ]
            if alpha != 255:
                red = red * alpha // 255
                green = green * alpha // 255
                blue = blue * alpha // 255
            if pixel_index % sample_stride == 0:
                sampled_pixels += 1
                channel_totals[0] += red
                channel_totals[1] += green
                channel_totals[2] += blue
                channel_minimums[0] = min(channel_minimums[0], red)
                channel_minimums[1] = min(channel_minimums[1], green)
                channel_minimums[2] = min(channel_minimums[2], blue)
                channel_maximums[0] = max(channel_maximums[0], red)
                channel_maximums[1] = max(channel_maximums[1], green)
                channel_maximums[2] = max(channel_maximums[2], blue)
                if max(red, green, blue) <= 8:
                    dark_count += 1
                if min(red, green, blue) >= 247:
                    bright_count += 1
                values = (red, green, blue)
                dominant = max(range(3), key=values.__getitem__)
                second = sorted(values)[1]
                if values[dominant] - second >= 80:
                    dominant_counts[dominant] += 1
            pixel_index += 1
        previous = current
    return {
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        "sampled_pixels": sampled_pixels,
        "channel_totals": channel_totals,
        "channel_minimums": channel_minimums,
        "channel_maximums": channel_maximums,
        "dark_count": dark_count,
        "bright_count": bright_count,
        "dominant_counts": dominant_counts,
    }


def _unfilter(
    current: bytearray,
    previous: bytearray,
    filter_type: int,
    bytes_per_pixel: int,
) -> None:
    if filter_type == 0:
        return
    for index in range(len(current)):
        left = current[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        above = previous[index]
        upper_left = (
            previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
        )
        if filter_type == 1:
            prediction = left
        elif filter_type == 2:
            prediction = above
        elif filter_type == 3:
            prediction = (left + above) // 2
        elif filter_type == 4:
            prediction = _paeth(left, above, upper_left)
        else:
            raise ValueError(f"unsupported PNG filter {filter_type}")
        current[index] = (current[index] + prediction) & 0xFF


def _paeth(left: int, above: int, upper_left: int) -> int:
    prediction = left + above - upper_left
    distance_left = abs(prediction - left)
    distance_above = abs(prediction - above)
    distance_upper_left = abs(prediction - upper_left)
    if distance_left <= distance_above and distance_left <= distance_upper_left:
        return left
    if distance_above <= distance_upper_left:
        return above
    return upper_left
