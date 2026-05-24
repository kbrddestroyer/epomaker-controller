def encode_rgb565(r: int, g: int, b: int) -> int:
    # Mask the bits of each color to fit the 5-6-5 format
    r_565 = (r & 0b11111000) << 8  # Red: 5 bits
    g_565 = (g & 0b11111100) << 3  # Green: 6 bits
    b_565 = (b & 0b11111000) >> 3  # Blue: 5 bits

    # Combine the channels into one 16-bit number
    rgb_565 = r_565 | g_565 | b_565

    return rgb_565


def decode_rgb565(pixel: int) -> tuple[int, int, int]:
    r = (pixel & 0xF800) >> 8  # Red: 5 bits
    g = (pixel & 0x07E0) >> 3  # Green: 6 bits
    b = (pixel & 0x001F) << 3  # Blue: 5 bits

    # We need to adjust them because we're expanding to 8 bits per channel
    r |= r >> 5
    g |= g >> 6
    b |= b >> 5

    return r, g, b