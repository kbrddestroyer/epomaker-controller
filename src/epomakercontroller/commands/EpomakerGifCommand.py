"""Command for sending animated GIFs natively to the Epomaker keyboard.

The keyboard firmware supports multi-frame image uploads. The protocol is:
- 1 init report: specifies frame count, per-frame data size, frame delay, and dimensions
- For each frame: 1000 data reports (type 0x38) + 1 footer report (type 0x34)
- Frame numbers are 1-based in data report headers

Init header byte layout (12 bytes), confirmed via USB sniffing:
  [0]     0xa5            Command ID (image upload)
  [1]     0x00            Sub-command
  [2]     uint8           Frame count (e.g. 0x12 = 18 frames; 0x01 for static)
  [3]     uint8           Frame delay in ms (e.g. 0x42 = 66ms; 0x00 for static)
  [4-5]   LE16            Per-frame data size (0xDAF4 = 56052 = 162*173*2 for full-res)
  [6]     0x00            Reserved
  [7]     checksum        0xFF - (sum(bytes[0:7]) & 0xFF)
  [8-11]  varies          Unknown trailing bytes (static: 00 00 a2 ad)

  Example (sniffed GIF):  a5 00 12 42 68 2e 00 70 0f 40 93 6d
  Example (static image): a5 00 01 00 f4 da 00 8b 00 00 a2 ad

Data report header (8 bytes):
  [0]     0x25            Report type
  [1]     0x00            Sub-type
  [2]     uint8           Frame number (1-based)
  [3]     0x00            Reserved
  [4-5]   LE16            Sequence number within frame (0-based)
  [6]     0x38/0x34       0x38 = data, 0x34 = last report of frame
  [7]     checksum
  [8-63]  56 bytes        Pixel data (RGB565)
"""

import math
import os
import cv2
import numpy as np
from PIL import Image

from .EpomakerCommand import EpomakerCommand, CommandStructure
from .EpomakerImageCommand import EpomakerImageCommand
from .data.constants import IMAGE_DIMENSIONS
from .reports.Report import Report, BUFF_LENGTH
from .reports.ReportWithData import ReportWithData
from ..logger.logger import Logger


# Screen dimensions
SCREEN_WIDTH, SCREEN_HEIGHT = IMAGE_DIMENSIONS  # (162, 173)

# Precompute all valid 4K-aligned dimension pairs (w, h) that fit the screen.
# per_frame_size = w * h * 2 must be a multiple of 4096.
_VALID_4K_DIMS: list[tuple[int, int, int]] = []  # (area, w, h)
for _w in range(32, SCREEN_WIDTH + 1):
    for _h in range(32, SCREEN_HEIGHT + 1):
        if (_w * _h * 2) % 4096 == 0:
            _VALID_4K_DIMS.append((_w * _h, _w, _h))
_VALID_4K_DIMS.sort(reverse=True)  # largest area first


class EpomakerGifCommand(EpomakerCommand):
    """A command for sending animated GIFs natively to the keyboard."""

    @staticmethod
    def best_gif_dimensions(source_width: int, source_height: int,
                            max_ar_error: float = 0.20) -> tuple[int, int]:
        """Find the largest 4K-aligned dimensions that fit the screen and
        best match the source GIF's aspect ratio.

        Args:
            source_width: Original GIF width in pixels.
            source_height: Original GIF height in pixels.
            max_ar_error: Maximum allowed aspect ratio deviation (0.20 = 20%).

        Returns:
            (width, height) tuple for the GIF upload.
        """
        target_ar = source_width / source_height
        for area, w, h in _VALID_4K_DIMS:
            ar = w / h
            if abs(ar - target_ar) / target_ar <= max_ar_error:
                return (w, h)
        # Fallback: largest available
        return (_VALID_4K_DIMS[0][1], _VALID_4K_DIMS[0][2])

    def __init__(self, n_frames: int, frame_delay_ms: int = 100,
                 gif_dimensions: tuple[int, int] = (160, 128)) -> None:
        self.n_frames = n_frames
        self.frame_delay_ms = frame_delay_ms
        self.gif_dimensions = gif_dimensions
        self.report_data_header_length = 8

        # Raw pixel data per frame MUST be a multiple of 4096.
        # The firmware's animation framebuffer uses 4K page alignment;
        # non-aligned sizes cause vertical line artifacts.
        raw_per_frame = gif_dimensions[0] * gif_dimensions[1] * 2
        if raw_per_frame % 4096 != 0:
            raise ValueError(
                f"GIF dimensions {gif_dimensions} produce per_frame_size="
                f"{raw_per_frame} which is not a multiple of 4096. "
                f"Choose dimensions where w*h*2 % 4096 == 0."
            )
        self.raw_per_frame = raw_per_frame

        data_payload = BUFF_LENGTH - 8  # 56 bytes
        # Use raw per_frame_size (matches sniffed Epomaker GIF protocol)
        self.per_frame_size = raw_per_frame
        self.reports_per_frame = math.ceil(raw_per_frame / data_payload)
        self.data_reports_per_frame = self.reports_per_frame - 1

        total_reports = n_frames * self.reports_per_frame
        structure = CommandStructure(
            number_of_starter_reports=1,
            number_of_data_reports=total_reports,
            number_of_footer_reports=0,
        )

        init_hex = self._build_init_header(n_frames, frame_delay_ms,
                                            self.per_frame_size, gif_dimensions)
        initial_report = Report(init_hex, index=0, checksum_index=None)
        super().__init__(initial_report, structure)

    @staticmethod
    def _build_init_header(n_frames: int, delay_ms: int,
                           per_frame_size: int,
                           gif_dimensions: tuple[int, int]) -> str:
        """Build the init report header hex string.

        Args:
            n_frames (int): Number of frames (max 255).
            delay_ms (int): Frame delay in milliseconds (max 255).
            per_frame_size (int): Bytes per frame.
            gif_dimensions (tuple): (width, height) of GIF frames.

        Returns:
            str: Hex string for the init report.
        """
        header_before_checksum = bytearray([
            0xa5, 0x00,
            n_frames & 0xFF,
            min(delay_ms, 255) & 0xFF,
            per_frame_size & 0xFF,
            (per_frame_size >> 8) & 0xFF,
            0x00,
        ])
        checksum = (0xFF - (sum(header_before_checksum) & 0xFF)) & 0xFF
        w, h = gif_dimensions
        full_header = header_before_checksum + bytearray([
            checksum,
            0x00, 0x00,
            w & 0xFF, h & 0xFF,
        ])
        return full_header.hex()

    @staticmethod
    def _prepare_frame_image(pil_frame: Image.Image,
                             dimensions: tuple[int, int]) -> np.ndarray:
        """Convert a PIL Image frame to a flattened RGB565 8-bit numpy array.

        Args:
            pil_frame (Image.Image): A PIL RGB image.
            dimensions (tuple): (width, height) to resize to.

        Returns:
            np.ndarray: Flattened uint8 array of RGB565 data.
        """
        rgb_array = np.array(pil_frame)
        bgr_array = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)

        image = cv2.resize(bgr_array, dimensions)
        image = cv2.flip(image, 0)
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        image_16bit = np.zeros(
            (image.shape[0], image.shape[1]), dtype=np.uint16
        )
        for y in range(image.shape[0]):
            for x in range(image.shape[1]):
                r, g, b = image[y, x]
                image_16bit[y, x] = EpomakerImageCommand._encode_rgb565(r, g, b)

        return np.ndarray.flatten(EpomakerCommand._np16_to_np8(image_16bit))

    @staticmethod
    def _extract_composited_frames(gif: Image.Image) -> list[Image.Image]:
        """Extract all frames from a GIF with proper compositing.

        Optimized GIFs use partial/diff frames where only changed pixels are
        stored, with transparency for unchanged areas. We must composite each
        frame onto a canvas to produce complete images, respecting disposal
        methods.

        Args:
            gif (Image.Image): An opened PIL GIF image.

        Returns:
            list[Image.Image]: List of fully composited RGB frames.
        """
        n_frames = getattr(gif, 'n_frames', 1)
        canvas = Image.new("RGBA", gif.size, (0, 0, 0, 255))
        frames: list[Image.Image] = []

        for i in range(n_frames):
            gif.seek(i)
            frame_rgba = gif.convert("RGBA")

            # Paste this frame onto the canvas; the alpha channel acts as the
            # mask so only non-transparent (changed) pixels are updated.
            canvas.paste(frame_rgba, (0, 0), frame_rgba)
            frames.append(canvas.copy().convert("RGB"))

            # Handle disposal method for the NEXT frame's base canvas
            disposal = gif.disposal_method if hasattr(gif, 'disposal_method') else 0
            if disposal == 2:
                # Restore to background colour (clear canvas)
                canvas = Image.new("RGBA", gif.size, (0, 0, 0, 255))
            # disposal 0/1 = keep canvas as-is (most common)
            # disposal 3 = restore to previous (rare; we approximate with keep)

        return frames

    def encode_gif(self, gif_path: str) -> None:
        """Encode all GIF frames using per-frame report structure.

        Each frame is encoded identically to a static image upload:
        1000 data reports (type 0x38, seq 0-999) + 1 footer report
        (type 0x34, seq 1000). Frame number increments per frame (1-based).
        This exactly matches the proven working static image protocol.
        """
        if not os.path.isfile(gif_path):
            Logger.log_error(f"Could not find GIF: {gif_path}")
            return

        try:
            gif = Image.open(gif_path)
        except Exception as e:
            Logger.log_error(f"Failed to open GIF: {e}")
            return

        actual_frames = getattr(gif, 'n_frames', 1)
        if actual_frames != self.n_frames:
            Logger.log_error(
                f"Expected {self.n_frames} frames, GIF has {actual_frames}."
            )
            return

        composited_frames = self._extract_composited_frames(gif)
        Logger.log_info(f"Extracted {len(composited_frames)} composited frames")

        data_buff_length = BUFF_LENGTH - self.report_data_header_length  # 56
        global_report_idx = 1  # init report is index 0

        for frame_idx, pil_frame in enumerate(composited_frames):
            try:
                raw_bytes = self._prepare_frame_image(
                    pil_frame, self.gif_dimensions
                ).tobytes()
                # No extra padding needed вЂ” per_frame_size equals raw pixel data
                frame_bytes = raw_bytes
            except Exception as e:
                Logger.log_error(f"Exception encoding frame {frame_idx}: {e}")
                return

            frame_number = frame_idx + 1  # 1-based
            data_pointer = 0

            # Data reports: seq 0 to (reports_per_frame-2), type 0x38
            for seq in range(self.data_reports_per_frame):
                seq_bytes = seq.to_bytes(2, "big")
                report = ReportWithData(
                    header_format_string=(
                        "2500{frame:02x}00"
                        "{seq_upper:02x}{seq_lower:02x}38"
                    ),
                    index=global_report_idx,
                    header_format_values={
                        "frame": frame_number,
                        "seq_upper": seq_bytes[1],
                        "seq_lower": seq_bytes[0],
                    },
                    checksum_index=7,
                )
                chunk = frame_bytes[data_pointer:data_pointer + data_buff_length]
                report.add_data(chunk)
                self._insert_report(report)
                data_pointer += data_buff_length
                global_report_idx += 1

            # Footer report: last seq, type 0x34
            footer_seq = self.data_reports_per_frame
            footer_seq_bytes = footer_seq.to_bytes(2, "big")
            footer_report = ReportWithData(
                header_format_string=(
                    "2500{frame:02x}00"
                    "{seq_upper:02x}{seq_lower:02x}34"
                ),
                index=global_report_idx,
                header_format_values={
                    "frame": frame_number,
                    "seq_upper": footer_seq_bytes[1],
                    "seq_lower": footer_seq_bytes[0],
                },
                checksum_index=7,
            )
            remaining = frame_bytes[data_pointer:]
            # Pad remaining to data_buff_length (52 real bytes + 4 zero padding)
            padded = bytes(remaining) + b'\x00' * (data_buff_length - len(remaining))
            footer_report.add_data(padded)
            self._insert_report(footer_report)
            global_report_idx += 1

            Logger.log_info(f"Encoded frame {frame_idx + 1}/{self.n_frames}")

        self.report_data_prepared = True
        # report_footer_prepared auto-True (number_of_footer_reports=0)

        expected_total = 1 + self.n_frames * self.reports_per_frame
        if len(self.reports) != expected_total:
            Logger.log_error(
                f"Expected {expected_total} total reports, got {len(self.reports)}."
            )