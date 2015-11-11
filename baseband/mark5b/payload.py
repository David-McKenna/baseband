"""
Definitions for VLBI Mark 5B payloads.

Implements a Mark5BPayload class used to store payload words, and decode to
or encode from a data array.

For the specification, see
http://www.haystack.edu/tech/vlbi/mark5/docs/Mark%205B%20users%20manual.pdf
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)
import numpy as np
from ..vlbi_base.payload import (VLBIPayloadBase, encode_2bit_real_base,
                                 decoder_levels, DTYPE_WORD)


__all__ = ['init_luts', 'decode_2bit_real', 'encode_2bit_real',
           'Mark5BPayload']


# Some duplication with mark4.py here: lut2bit = mark4.lut2bit1
# Though lut1bit = -mark4.lut1bit, so perhaps not worth combining.
def init_luts():
    """Set up the look-up tables for levels as a function of input byte.

    For 1-bit mode, one has just the sign bit:
      === =====
       s  value
      === =====
       0  -1
       1  +1
      === =====

    For 2-bit mode, there is a sign and a magnitude, which encode:
     === === ===== =====
      m   s  value s*2+m
     === === ===== =====
      0   0  -Hi    0
      0   1  +1     2
      1   0  -1     1
      1   1  +Hi    3
     === === ===== =====

    See table 13 in
    https://science.nrao.edu/facilities/vlba/publications/memos/upgrade/sensimemo13.pdf
    and
    http://www.haystack.edu/tech/vlbi/mark5/docs/Mark%205B%20users%20manual.pdf
    Appendix A: sign always on even bit stream (0, 2, 4, ...), and magnitude
    on adjacent odd stream (1, 3, 5, ...).

    In the above table, the last column is the index in the linearly increasing
    table of levels (``decoder_levels[2]``).
    """
    b = np.arange(256)[:, np.newaxis]
    l = np.arange(8)
    lut1bit = decoder_levels[1][((b >> l) & 1)]
    # 2-bit mode: sign bit in lower position thatn magnitude bit
    # ms=00,01,10,11 = -Hi, 1, -1, Hi (lut
    s = np.arange(0, 8, 2)  # 0, 2, 4, 6
    m = s+1                 # 1, 3, 5, 7
    l = (((b >> s) & 1) << 1) + ((b >> m) & 1)
    lut2bit = decoder_levels[2][l]
    return lut1bit, lut2bit

lut1bit, lut2bit = init_luts()


# def decode_1bit(frame, nvlbichan):
#     return lut1bit[frame].reshape(-1, nvlbichan)


# Decoders keyed by bits_per_sample, complex_data:
def decode_2bit_real(words, out=None):
    b = words.view(np.uint8)
    if out is None:
        return lut2bit.take(b, axis=0).ravel()
    else:
        outf4 = out.reshape(-1, 4)
        assert outf4.base is out or outf4.base is out.base
        lut2bit.take(b, axis=0, out=outf4)
        return out


shift2bit = np.arange(0, 8, 2).astype(np.uint8)
reorder = np.array([0, 2, 1, 3], dtype=np.uint8)


def encode_2bit_real(values):
    bitvalues = encode_2bit_real_base(values.reshape(-1, 4))
    # swap 1 & 2
    reorder.take(bitvalues, out=bitvalues)
    bitvalues <<= shift2bit
    return np.bitwise_or.reduce(bitvalues, axis=-1).view(DTYPE_WORD)

encode_2bit_real.__doc__ = encode_2bit_real_base.__doc__


class Mark5BPayload(VLBIPayloadBase):
    """Container for decoding and encoding VDIF payloads.

    Parameters
    ----------
    words : ndarray
        Array containg LSB unsigned words (with the right size) that
        encode the payload.
    nchan : int
        Number of channels in the data.  Default: 1.
    bps : int
        Number of bits per sample.  Default: 2.
    """

    _size = 2500 * 4
    _encoders = {(2, False): encode_2bit_real}
    _decoders = {(2, False): decode_2bit_real}

    def __init__(self, words, nchan=1, bps=2):
        super(Mark5BPayload, self).__init__(words, nchan, bps,
                                            complex_data=False)

    @classmethod
    def fromdata(cls, data, bps=2):
        """Encode data as payload, using a given number of bits per sample.

        It is assumed that the last dimension is the number of channels.
        """
        if data.dtype.kind == 'c':
            raise ValueError("Mark5B format does not support complex data.")
        encoder = cls._encoders[bps, False]
        return cls(encoder(data.ravel()), nchan=data.shape[-1], bps=bps)