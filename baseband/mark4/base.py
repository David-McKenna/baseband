# Licensed under the GPLv3 - see LICENSE.rst
import os
import io

import numpy as np
from astropy.time import Time

from ..vlbi_base.base import VLBIStreamReaderBase, VLBIStreamWriterBase
from .header import Mark4Header
from .frame import Mark4Frame


__all__ = ['Mark4FileReader', 'Mark4FileWriter', 'Mark4StreamReader',
           'Mark4StreamWriter', 'open']

# Look-up table for the number of bits in a byte.
nbits = ((np.arange(256)[:, np.newaxis] >> np.arange(8) & 1)
         .sum(1).astype(np.int16))


class Mark4FileReader(io.BufferedReader):
    """Simple reader for Mark 4 files.

    Adds ``read_frame`` and ``find_frame`` methods to the basic binary file
    reader :class:`~io.BufferedReader`.
    """

    def read_frame(self, ntrack, decade=None):
        """Read a single frame (header plus payload).

        Parameters
        ----------
        ntrack : int
            Number of Mark 4 bitstreams.
        decade : int, optional
            Decade the observations were taken (needed to remove ambiguity in
            the Mark 4 time stamp).  If not given, it is inferred from the
            decade of the file's creation time.

        Returns
        -------
        frame : `~baseband.mark4.Mark4Frame`
            With ``.header`` and ``.data`` properties that return the
            :class:`~baseband.mark4.Mark4Header` and data encoded in the frame,
            respectively.
        """
        if decade is None:
            if not hasattr(self, '_creation_time_isot'):
                self._creation_time_isot = Time(os.path.getctime(self.name),
                                                format='unix').isot
            decade = int(self._creation_time.isot[:3]) * 10
        elif isinstance(decade, Time):
            decade = int(decade.isot[:3]) * 10
        return Mark4Frame.fromfile(self, ntrack=ntrack, decade=decade)

    def find_frame(self, ntrack=None, template_header=None,
                   maximum=None, forward=True):
        """Look for the first occurrence of a frame, from the current position.

        The search is for the following pattern:

        * 32*tracks bits set at offset bytes
        * 32*tracks bits set at offset+2500*tracks bytes
        * 1*tracks bits unset before offset+2500*tracks bytes

        Parameters
        ----------
        maximum : int, optional
            Maximum number of bytes forward to search through.
            Default is the framesize (20000 * ntrack // 8).
        forward : bool, optional
            Whether to search forwards or backwards.  Default is forwards.

        Returns
        -------
        offset : int
        """
        if template_header:
            ntrack = template_header.ntrack
        nset = np.ones(32 * ntrack // 8, dtype=np.int16)
        nunset = np.ones(ntrack // 8, dtype=np.int16)
        b = ntrack * 2500
        a = b - ntrack // 8
        if maximum is None:
            maximum = 2 * ntrack * 20000 // 8
        # Loop over chunks to try to find the frame marker.
        step = b // 25
        file_pos = self.tell()
        if forward:
            iterate = range(file_pos, file_pos + maximum, step)
        else:
            iterate = range(file_pos - b - step - len(nset),
                            file_pos - b - step - len(nset) - maximum, -step)
        for frame in iterate:
            self.seek(frame)
            data = np.fromstring(self.read(b+step+len(nset)),
                                 dtype=np.uint8)
            if len(data) < b + step + len(nset):
                break
            databits1 = nbits[data[:step+len(nset)]]
            lownotset = np.convolve(databits1 < 6, nset, 'valid')
            databits2 = nbits[data[b:]]
            highnotset = np.convolve(databits2 < 6, nset, 'valid')
            databits3 = nbits[data[a:a+step+len(nunset)]]
            highnotunset = np.convolve(databits3 > 1, nunset, 'valid')
            wrong = lownotset + highnotset + highnotunset
            try:
                extra = np.where(wrong == 0)[0][0 if forward else -1]
            except IndexError:
                continue
            else:
                frame_start = frame + extra - 32 * 2 * ntrack // 8
                self.seek(frame_start)
                return frame_start

        self.seek(file_pos)
        return None


class Mark4FileWriter(io.BufferedWriter):
    """Simple writer for Mark 4 files.

    Adds ``write_frame`` method to the basic binary file writer
    :class:`~io.BufferedWriter`.
    """
    def write_frame(self, data, header=None, **kwargs):
        """Write a single frame (header plus payload).

        Parameters
        ----------
        data : array or `~baseband.mark4.Mark4Frame`
            If an array, a header should be given, which will be used to
            get the information needed to encode the array, and to construct
            the Mark 4 frame.
        header : `~baseband.mark4.Mark4Header`
            Ignored if payload is a :class:`~baseband.mark4.Mark4Frame`
            instance.
        **kwargs :
            If no header is given, these are used to initialize one.
        """
        if not isinstance(data, Mark4Frame):
            if header is None:
                header = Mark4Header.fromvalues(**kwargs)
            data = Mark4Frame.fromdata(data, header)
        return data.tofile(self)


class Mark4StreamReader(VLBIStreamReaderBase):
    """VLBI Mark 4 format reader.

    This wrapper is allows one to access a Mark 4 file as a continues series
    of samples.  Note that possible gaps in the data stream are not filled in,
    though parts of the data stream replaced by header values are filled in.

    Parameters
    ----------
    raw : str, filehandle, or `~baseband.mark4.Mark4FileReader`
        file name, or file handle to raw data file.
    ntrack : int
        Number of tracks used to store the data.
    decade : int, or `~astropy.time.Time`
        Year rounded to decade, to remove ambiguities in the time stamps.
        By default, it will be inferred from the file creation date.
    thread_ids: list of int, optional
        Specific threads/channels to read.  By default, all are read.
    sample_rate : `~astropy.units.Quantity`, optional
        Rate at which each thread is sampled (bandwidth * 2; frequency units).
        If not given, it will be determined from the frame rate.
    """

    _frame_class = Mark4Frame

    def __init__(self, raw, ntrack, decade=None, thread_ids=None,
                 sample_rate=None):
        if not hasattr(raw, 'read'):
            raw = io.open(raw, mode='rb')
        if not isinstance(raw, Mark4FileReader):
            raw = Mark4FileReader(raw)
        self.offset0 = raw.find_frame(ntrack=ntrack)
        self._frame = raw.read_frame(ntrack, decade)
        self._frame_data = None
        self._frame_nr = None
        header = self._frame.header
        bps = header.bps
        nchan = header.nchan
        super(Mark4StreamReader, self).__init__(
            fh_raw=raw, header0=header, nchan=nchan, bps=bps,
            thread_ids=thread_ids,
            samples_per_frame=header.framesize * 8 // bps // nchan,
            sample_rate=sample_rate)

    def read(self, count=None, fill_value=0., squeeze=True, out=None):
        """Read count samples.

        The range retrieved can span multiple frames.

        Parameters
        ----------
        count : int
            Number of samples to read.  If omitted or negative, the whole
            file is read.
        fill_value : float
            Value to use for invalid or missing data.
        squeeze : bool
            If `True` (default), remove channel and thread dimensions if unity.
        out : `None` or array
            Array to store the data in. If given, count will be inferred,
            and squeeze is set to `False`.

        Returns
        -------
        out : array of float
            Dimensions are (sample-time, vlbi-thread, channel).
        """
        if out is None:
            if count is None or count < 0:
                count = self.size - self.offset

            out = np.empty((self.nthread, count),
                           dtype=self._frame.dtype).T
        else:
            count = out.shape[0]
            squeeze = False

        offset0 = self.offset
        while count > 0:
            frame_nr, sample_offset = divmod(self.offset,
                                             self.samples_per_frame)
            if frame_nr != self._frame_nr:
                # Read relevant frame, reusing data array from previous frame.
                self._read_frame(frame_nr, out=self._frame_data)

            data = self._frame.data
            if self.thread_ids:
                data = data[:, self.thread_ids]
            # Copy relevant data from frame into output.
            nsample = min(count, self.samples_per_frame - sample_offset)
            sample = self.offset - offset0
            out[sample:sample + nsample] = data[sample_offset:
                                                sample_offset + nsample]
            self.offset += nsample
            count -= nsample

        return out.squeeze() if squeeze else out

    def _read_frame(self, frame_nr=None, out=None):
        if frame_nr is None:
            frame_nr = self.offset // self.samples_per_frame
        self.fh_raw.seek(self.offset0 + frame_nr * self.header0.framesize)
        self._frame = self.fh_raw.read_frame(ntrack=self.header0.ntrack,
                                             decade=self.header0.decade)
        # Convert payloads to data array.
        self._frame_data = self._frame.todata(data=out)
        self._frame_nr = frame_nr
        return self._frame_data


class Mark4StreamWriter(VLBIStreamWriterBase):
    """VLBI Mark 4 format writer.

    Parameters
    ----------
    raw : filehandle, or name.
        Should be a :class:`Mark4FileWriter` or :class:`~io.BufferedWriter`
        instance. If a name, will get opened for writing binary data.
    sample_rate : `~astropy.units.Quantity`
        Rate at which each thread is sampled (bandwidth * 2; frequency units).
        This is needed to calculate time stamps.
    header : `~baseband.mark4.Mark4Header`
        Header for the first frame, holding start time information, etc.
    **kwargs
        If no header is give, an attempt is made to construct the header from
        these.  For a standard header, this would include the following.

    --- Header keywords : (see :meth:`~baseband.mark4.Mark4Header.fromvalues`)

    time : `~astropy.time.Time`
        Sets bcd-encoded unit year, day, hour, minute, second.
    ntrack : int
        Number of Mark 4 bitstreams (equal to number of channels times
        ``fanout`` times ``bps``)
    bps : int
        Bits per sample.
    fanout : int
        Number of tracks over which a given channel is spread out.
    """

    _frame_class = Mark4Frame

    def __init__(self, raw, sample_rate, header=None, **kwargs):
        if isinstance(raw, io.BufferedWriter):
            if not isinstance(raw, Mark4FileWriter):
                raw = Mark4FileWriter(raw)
        else:
            raw = Mark4FileWriter(io.open(raw, mode='wb'))
        if header is None:
            header = Mark4Header.fromvalues(**kwargs)
        super(Mark4StreamWriter, self).__init__(
            fh_raw=raw, header0=header, thread_ids=range(header.nchan),
            bps=header.bps, nchan=header.nchan,
            samples_per_frame=(header.framesize * 8 // header.bps //
                               header.nchan),
            sample_rate=sample_rate)

        self._data = np.zeros((self.samples_per_frame, self.nchan), np.float32)

    def write(self, data, squeezed=True, invalid_data=False):
        """Write data, buffering by frames as needed."""
        if squeezed and data.ndim < 2:
            data = np.expand_dims(data, axis=1 if self.nthread == 1 else 0)

        assert data.shape[1] == self.nthread

        count = data.shape[0]
        sample = 0
        offset0 = self.offset
        frame = self._data
        while count > 0:
            frame_nr, sample_offset = divmod(self.tell(),
                                             self.samples_per_frame)
            if sample_offset == 0:
                # set up header for new frame.
                self._header = self.header0.copy()
                self._header.time = self.tell(unit='time')

            if invalid_data:
                # Mark whole frame as invalid data.
                self._header['communication_error'] = True

            nsample = min(count, self.samples_per_frame - sample_offset)
            sample_end = sample_offset + nsample
            sample = self.offset - offset0
            frame[sample_offset:sample_end] = data[sample:sample + nsample]
            if sample_end == self.samples_per_frame:
                self.fh_raw.write_frame(self._data, self._header)

            self.offset += nsample
            count -= nsample


def open(name, mode='rs', **kwargs):
    """Open VLBI Mark 4 format file for reading or writing.

    Opened as a binary file, one gets a standard file handle, but with
    methods to read/write a frame.  Opened as a stream, the file handler
    is wrapped, allowing access to it as a series of samples.

    Parameters
    ----------
    name : str
        File name
    mode : {'rb', 'wb', 'rs', or 'ws'}, optional
        Whether to open for reading or writing, and as a regular binary file
        or as a stream (default is reading a stream).
    **kwargs
        Additional arguments when opening the file as a stream

    --- For reading a stream : (see `~baseband.mark4.base.Mark4StreamReader`)

    ntrack : int
        Number of tracks used to store the data.
    thread_ids: list of int, optional
        Specific threads/channels to read.  By default, all are read.
    sample_rate : `~astropy.units.Quantity`, optional
        Rate at which each thread is sampled (bandwidth * 2; frequency units).
        If not given, it will be determined from the frame rate.

    --- For writing a stream : (see `~baseband.mark4.base.Mark4StreamWriter`)

    sample_rate : `~astropy.units.Quantity`
        Rate at which each thread is sampled (bandwidth * 2; frequency units).
        This is needed to calculate time stamps.
    header : `~baseband.mark4.Mark4Header`
        Header for the first frame, holding time information, etc.
    **kwargs
        If the header is not given, an attempt will be made to construct one
        with any further keyword arguments.  See
        :class:`~baseband.mark4.base.Mark4StreamWriter`.

    Returns
    -------
    Filehandle
        :class:`~baseband.mark4.base.Mark4FileReader` or
        :class:`~baseband.mark4.base.Mark4FileWriter` instance (binary), or
        :class:`~baseband.mark4.base.Mark4StreamReader` or
        :class:`~baseband.mark4.base.Mark4StreamWriter` instance (stream)
    """
    if 'w' in mode:
        if not hasattr(name, 'write'):
            name = io.open(name, 'wb')
        fh = Mark4FileWriter(name)
        return fh if 'b' in mode else Mark4StreamWriter(fh, **kwargs)
    elif 'r' in mode:
        if not hasattr(name, 'read'):
            name = io.open(name, 'rb')
        fh = Mark4FileReader(name)
        return fh if 'b' in mode else Mark4StreamReader(fh, **kwargs)
    else:
        raise ValueError("Only support opening Mark 4 file for reading "
                         "or writing (mode='r' or 'w').")