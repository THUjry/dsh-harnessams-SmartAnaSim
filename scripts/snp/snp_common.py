# -*- coding: utf-8 -*-
"""snp_common.py -- shared S-parameter file I/O and math for the snp_* tools.

Library module (no main), imported by scripts/snp/snp_read.py and
scripts/snp/snp_extract.py. Provides:

* loaders for three input formats into one unified in-memory form
  (class SNPData: frequency list + per-point NxN complex matrices + parameter
  kind + per-port reference impedance):
    - Touchstone v1 (.s1p ... .sNp): '#' option line (freq unit / RI-MA-DB /
      scalar R), '!' comments, free continuation, noise-section leftovers
      ignored with a warning. Data ordering follows the v1 spec: 2-port files
      are S11,S21,S12,S22; N>2 files are row-major (S11..S1n, S21..S2n, ...).
    - Spectre psfascii sp output (sp.sp): TRACE/VALUE sections; when the file
      carries several parameter kinds (S/Y/Z) S is preferred, then Z, then Y;
      per-port reference impedances are taken from the PORT<k>:r header lines.
    - params CSV written by snp_read.py ('#' metadata header + long-format
      rows freq_hz,i,j,re,im) for lossless round-tripping.
* numeric backend adapter: matrix inv/matmul funnel through numpy when
  importable; otherwise the bundled pure-stdlib assets/numfallback.py is used
  (also forced by environment variable SNP_FORCE_FALLBACK=1). All upper-level
  code runs identically on both backends.
* parameter conversions (general real per-port z0):
      Z = sqrt(Z0) (I+S) (I-S)^-1 sqrt(Z0),   S = (W-I)(W+I)^-1, W_ij = Z_ij/sqrt(z0i z0j),
      Y = Z^-1,   and 2-port Z -> ABCD (A=Z11/Z21, B=detZ/Z21, C=1/Z21, D=Z22/Z21).
* port-reduction primitives shared by every device extractor:
      - schur_ground: eliminate ports forced to V=0 (extraction-time
        single-ended grounding) via the Schur complement;
      - sign_reduce: build the reduced device matrix from per-role "sign
        vectors" (a differential terminal pair maps to [+1,-1], a single-ended
        port to [+1]; a floating center tap simply maps to nothing, i.e. I=0).
* small math helpers: complex sqrt with Re>0 branch (passive impedances),
  1-D phase unwrap, first positive-to-negative zero crossing (SRF), and the
  ADE gmax (MAG/MSG) of a 2-port S set.

Conventions: frequencies are Hz floats as given (no re-sorting); complex
matrices are nested Python lists of complex; exceptions carry short English
messages suitable for CLI stderr.
"""

import cmath
import json
import math
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# numeric backend: numpy primary, bundled assets/numfallback.py fallback
# ---------------------------------------------------------------------------


def _load_backend():
    here = Path(__file__).resolve().parent
    forced = os.environ.get("SNP_FORCE_FALLBACK") == "1"
    if not forced:
        try:
            import numpy as _np

            def _inv(a):
                return _np.linalg.inv(_np.array(a, dtype=complex)).tolist()

            def _mul(a, b):
                return (_np.array(a, dtype=complex) @ _np.array(b, dtype=complex)).tolist()

            return _inv, _mul, "numpy"
        except ImportError:
            pass
    sys.path.insert(0, str(here.parents[1] / "assets"))
    import numfallback
    return numfallback.inv, numfallback.matmul, "fallback"


INV, MATMUL, BACKEND = _load_backend()


class SnpError(Exception):
    """User-facing input/consistency error (maps to CLI exit code 2)."""


# ---------------------------------------------------------------------------
# unified data container
# ---------------------------------------------------------------------------


class SNPData(object):
    """Parsed network data: per-frequency NxN complex matrices.

    Attributes:
        freq    list[float]        frequency in Hz (order as given)
        mats    list[list[list[complex]]]  matrices, mats[k][i][j]
        param   str                "S" | "Y" | "Z"
        z0      list[float]        per-port reference impedance (S only)
        source  str                origin file path
        fmt_desc str               one-line provenance for summaries
        warnings list[str]
    """

    def __init__(self, freq, mats, param, z0, source, fmt_desc, warnings=None):
        self.freq = freq
        self.mats = mats
        self.param = param
        self.z0 = z0
        self.source = source
        self.fmt_desc = fmt_desc
        self.warnings = list(warnings or [])

    @property
    def n_ports(self):
        return len(self.mats[0]) if self.mats else 0

    def check_reciprocity(self):
        """max |Zij-Zji| / max |Zij| (or S for S-kind data); 0.0 for 1-port."""
        n = self.n_ports
        if n < 2:
            return 0.0
        worst = 0.0
        top = 0.0
        for m in self.mats:
            for i in range(n):
                for j in range(n):
                    a = abs(m[i][j])
                    if a > top:
                        top = a
                    if j > i:
                        d = abs(m[i][j] - m[j][i])
                        if d > worst:
                            worst = d
        return worst / top if top > 0 else 0.0


# ---------------------------------------------------------------------------
# loader: Touchstone v1
# ---------------------------------------------------------------------------

_FREQ_SCALE = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


def _ts_port_count(path):
    m = re.search(r"\.s(\d+)p$", path.name, re.IGNORECASE)
    if not m:
        raise SnpError(
            "touchstone filename must match .s<N>p (got %r); the port count "
            "is taken from the name" % path.name)
    n = int(m.group(1))
    if n < 1:
        raise SnpError("touchstone port count must be >= 1: %s" % path.name)
    return n


def _ts_pair_order(n):
    """File emission order of matrix entries as (row, col) indices."""
    if n == 1:
        return [(0, 0)]
    if n == 2:
        # Touchstone v1 2-port special case: S11, S21, S12, S22 (column major).
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    # v1 n-port (n > 2): row major S11..S1n, S21..S2n, ...
    return [(i, j) for i in range(n) for j in range(n)]


def _ts_to_complex(a, b, fmt):
    if fmt == "MA":
        return a * cmath.exp(1j * math.radians(b))
    if fmt == "DB":
        return (10.0 ** (a / 20.0)) * cmath.exp(1j * math.radians(b))
    return complex(a, b)  # RI


def parse_touchstone(path):
    """Parse a Touchstone v1 .sNp file -> SNPData (param="S")."""
    path = Path(path)
    n = _ts_port_count(path)
    funit, fmt, rref = "HZ", "MA", 50.0
    saw_option = False
    nums = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise SnpError("cannot read %s: %s" % (path, e))
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("!"):
            continue
        if line.startswith("#"):
            saw_option = True
            tk = line.lstrip("#! \t").upper().split()
            if tk and tk[0] in _FREQ_SCALE:
                funit = tk[0]
            for word in ("RI", "MA", "DB"):
                if word in tk:
                    fmt = word
            if "R" in tk:
                pos = tk.index("R")
                if pos + 1 < len(tk):
                    try:
                        rref = float(tk[pos + 1])
                    except ValueError:
                        pass
            continue
        # inline trailing comments are rare but legal
        bang = line.find("!")
        if bang >= 0:
            line = line[:bang].strip()
        if not line:
            continue
        for tok in line.split():
            try:
                # tolerate Fortran-style D exponents
                nums.append(float(tok.replace("D", "E").replace("d", "e")))
            except ValueError:
                raise SnpError("non-numeric token %r in %s" % (tok, path))
    warnings = []
    if not saw_option:
        warnings.append("no '#' option line: defaults HZ/MA/R 50 assumed")
    ncol = 1 + 2 * n * n
    if len(nums) < ncol:
        raise SnpError("touchstone data truncated: %d numbers, need %d per point"
                       % (len(nums), ncol))
    npts = len(nums) // ncol
    leftover = len(nums) - npts * ncol
    if leftover:
        warnings.append("ignored %d trailing number(s) beyond whole frequency "
                        "points (noise data?)" % leftover)
    scale = _FREQ_SCALE[funit]
    order = _ts_pair_order(n)
    freq, mats = [], []
    idx = 0
    for _k in range(npts):
        f = nums[idx] * scale
        idx += 1
        m = [[0j] * n for _ in range(n)]
        for (i, j) in order:
            a, b = nums[idx], nums[idx + 1]
            idx += 2
            m[i][j] = _ts_to_complex(a, b, fmt)
        freq.append(f)
        mats.append(m)
    if npts > 1 and any(freq[i] >= freq[i + 1] for i in range(npts - 1)):
        warnings.append("frequencies not strictly ascending (kept as given)")
    desc = "touchstone v1 %s S %s R %g, %d pts" % (funit, fmt, rref, npts)
    return SNPData(freq, mats, "S", [rref] * n, str(path), desc, warnings)


# ---------------------------------------------------------------------------
# loader: Spectre psfascii sp
# ---------------------------------------------------------------------------

_PSF_VAL_RE = re.compile(r'^"(\w+)"\s*\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)')
_PSF_FREQ_RE = re.compile(r'^"freq"\s+([-\d.eE+]+)')
_PSF_PORT_RE = re.compile(r'^"PORT(\d+):(r|num)"\s+([-\d.eE+]+)')
_PSF_NPORT_RE = re.compile(r'^"number of ports"\s+(\d+)')


def parse_psfascii(path):
    """Parse a Spectre psfascii sp analysis file -> SNPData.

    When several parameter kinds are present (the XFMR/Lange files carry S, Y
    and Z), S is preferred (converted with the per-port impedances from the
    header), then Z, then Y.
    """
    path = Path(path)
    trace = []           # ordered trace names
    data = {}            # name -> list of complex
    freq = []
    nport_hdr = None
    r_by_idx = {}        # PORT<k>:r  -> impedance
    num_by_idx = {}      # PORT<k>:num -> 1-based port number
    section = None
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError as e:
        raise SnpError("cannot read %s: %s" % (path, e))
    with fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            if s in ("HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END"):
                section = s
                continue
            if section == "HEADER":
                m = _PSF_NPORT_RE.match(s)
                if m:
                    nport_hdr = int(m.group(1))
                    continue
                m = _PSF_PORT_RE.match(s)
                if m:
                    if m.group(2) == "r":
                        r_by_idx[int(m.group(1))] = float(m.group(3))
                    else:
                        num_by_idx[int(m.group(1))] = int(m.group(3))
                continue
            if section == "TRACE":
                m = re.match(r'^"(\w+)"', s)
                if m and m.group(1) not in data:
                    trace.append(m.group(1))
                    data[m.group(1)] = []
                continue
            if section == "VALUE":
                mf = _PSF_FREQ_RE.match(s)
                if mf:
                    freq.append(float(mf.group(1)))
                    continue
                mv = _PSF_VAL_RE.match(s)
                if mv:
                    name = mv.group(1)
                    if name not in data:
                        trace.append(name)
                        data[name] = []
                    data[name].append(complex(float(mv.group(2)), float(mv.group(3))))
                continue
            if section == "END":
                break
    if not freq:
        raise SnpError("no VALUE/freq points in %s" % path)
    z_by_port = {}
    for idx, pnum in num_by_idx.items():
        z_by_port[pnum] = r_by_idx.get(idx, 50.0)
    for idx, r in r_by_idx.items():
        z_by_port.setdefault(idx + 1, r)

    def detect_n(kind):
        n = 0
        for name in data:
            m = re.match(r"^%s(\d)(\d)$" % kind, name)
            if m:
                n = max(n, int(m.group(1)), int(m.group(2)))
        return n

    kind = None
    for k in ("s", "z", "y"):
        if detect_n(k) >= 1:
            kind = k.upper()
            break
    if kind is None:
        raise SnpError("no S/Z/Y traces found in %s" % path)
    n = detect_n(kind.lower()) or nport_hdr or 1
    missing = ["%s%d%d" % (kind.lower(), i + 1, j + 1)
               for i in range(n) for j in range(n)
               if len(data.get("%s%d%d" % (kind.lower(), i + 1, j + 1), [])) != len(freq)]
    if missing:
        raise SnpError("incomplete %s-param traces in %s: %s"
                       % (kind, path, ",".join(missing[:4])))
    warnings = []
    z0 = [z_by_port.get(p + 1, 50.0) for p in range(n)]
    if len(set(round(v, 9) for v in z0)) > 1:
        warnings.append("per-port reference impedances differ: %s (general "
                        "sqrt(z0i*z0j) conversion applied)" % z0)
    mats = []
    for k in range(len(freq)):
        m = [[0j] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                m[i][j] = data["%s%d%d" % (kind.lower(), i + 1, j + 1)][k]
        mats.append(m)
    desc = "psfascii sp (%s-params, %d pts, z0=%s)" % (kind, len(freq), z0[0])
    return SNPData(freq, mats, kind, z0, str(path), desc, warnings)


# ---------------------------------------------------------------------------
# loader/writer: snp_read params CSV
# ---------------------------------------------------------------------------

def write_params_csv(data, out_path):
    """Write per-frequency matrices as long-format CSV with a '#' metadata
    header (round-trippable by parse_params_csv)."""
    n = data.n_ports
    lines = ["# snp_read params dump v1",
             "# source=%s" % data.source.replace("\n", " "),
             "# param=%s" % data.param,
             "# n_ports=%d" % n,
             "# zref=%s" % ",".join("%.10g" % v for v in data.z0)]
    lines.append("freq_hz,i,j,re,im")
    for k, f in enumerate(data.freq):
        m = data.mats[k]
        for i in range(n):
            for j in range(n):
                v = m[i][j]
                lines.append("%.10e,%d,%d,%.12e,%.12e" % (f, i + 1, j + 1, v.real, v.imag))
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def parse_params_csv(path):
    """Read back a params CSV written by write_params_csv -> SNPData."""
    path = Path(path)
    meta = {}
    rows = []
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError as e:
        raise SnpError("cannot read %s: %s" % (path, e))
    with fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                body = s.lstrip("#").strip()
                if "=" in body:
                    k, v = body.split("=", 1)
                    meta[k.strip()] = v.strip()
                continue
            if s.startswith("freq_hz"):
                continue
            parts = s.split(",")
            if len(parts) != 5:
                raise SnpError("bad params CSV row in %s: %r" % (path, s))
            rows.append((float(parts[0]), int(parts[1]) - 1, int(parts[2]) - 1,
                         float(parts[3]), float(parts[4])))
    if "param" not in meta or "n_ports" not in meta:
        raise SnpError("params CSV %s lacks metadata header (not written by snp_read?)" % path)
    n = int(meta["n_ports"])
    param = meta["param"].upper()
    zref = [float(v) for v in meta.get("zref", "50").split(",")]
    freqs = []
    seen = {}
    for f, i, j, re_, im_ in rows:
        if j == 0 and i == 0:
            freqs.append(f)
        seen.setdefault(f, {})[(i, j)] = complex(re_, im_)
    mats = []
    for f in freqs:
        block = seen[f]
        if len(block) != n * n:
            raise SnpError("params CSV %s: incomplete matrix at %g Hz" % (path, f))
        mats.append([[block[(i, j)] for j in range(n)] for i in range(n)])
    desc = "params csv (%s, %d pts)" % (param, len(freqs))
    return SNPData(freqs, mats, param, zref, str(path), desc, [])


# ---------------------------------------------------------------------------
# format sniffing
# ---------------------------------------------------------------------------


def load_any(path):
    """Load a .sNp / psfascii sp / params-CSV file by extension + content."""
    path = Path(path)
    if not path.is_file():
        raise SnpError("file not found: %s" % path)
    name = path.name.lower()
    if name.endswith(".csv"):
        return parse_params_csv(path)
    if re.search(r"\.s\d+p$", name):
        return parse_touchstone(path)
    if name.endswith(".sp") or name.endswith(".sp.sp"):
        return parse_psfascii(path)
    # fall back to content sniffing
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError as e:
        raise SnpError("cannot read %s: %s" % (path, e))
    if re.search(r"^\s*(HEADER|TRACE|VALUE)\s*$", head, re.MULTILINE):
        return parse_psfascii(path)
    if re.search(r"^#.*\bS\b", head, re.MULTILINE) or re.match(r"^\s*[-+0-9.eE]", head):
        # touchstone content without .sNp name: port count unknowable -> error
        raise SnpError("cannot determine port count: %s is not a .sNp / psfascii / "
                       "params-CSV file (rename to .s<N>p)" % path)
    raise SnpError("unrecognized input format: %s" % path)


# ---------------------------------------------------------------------------
# conversions (all go through Z; general real per-port z0)
# ---------------------------------------------------------------------------


def _eye(n):
    return [[(1.0 + 0j) if i == j else 0j for j in range(n)] for i in range(n)]


def _madd(a, b):
    n = len(a)
    return [[a[i][j] + b[i][j] for j in range(n)] for i in range(n)]


def _msub(a, b):
    n = len(a)
    return [[a[i][j] - b[i][j] for j in range(n)] for i in range(n)]


def _m_scale_outer(a, s):
    """a_ij * s_i * s_j"""
    n = len(a)
    return [[a[i][j] * s[i] * s[j] for j in range(n)] for i in range(n)]


def to_z(data, drop_bad=True):
    """Convert SNPData to Z-parameters. Points where the conversion is
    singular are dropped (with a warning) when drop_bad, else raise."""
    if data.param == "Z":
        return SNPData(list(data.freq), [ [row[:] for row in m] for m in data.mats],
                       "Z", data.z0, data.source, data.fmt_desc, list(data.warnings))
    n = data.n_ports
    sq = [math.sqrt(v) for v in data.z0[:n]] if len(data.z0) >= n else [math.sqrt(50.0)] * n
    freq, mats, warns = [], [], list(data.warnings)
    for k, m in enumerate(data.mats):
        try:
            if data.param == "S":
                im = _eye(n)
                i_s = _msub(im, m)
                w = MATMUL(_madd(im, m), INV(i_s))
                z = _m_scale_outer(w, sq)
            elif data.param == "Y":
                z = INV(m)
            else:
                raise SnpError("unknown param kind %r" % data.param)
        except ZeroDivisionError as e:
            if not drop_bad:
                raise SnpError("singular conversion at %g Hz: %s" % (data.freq[k], e))
            warns.append("dropped point %g Hz (singular %s->Z)" % (data.freq[k], data.param))
            continue
        freq.append(data.freq[k])
        mats.append(z)
    if not mats:
        raise SnpError("every point is singular; cannot convert to Z")
    return SNPData(freq, mats, "Z", data.z0, data.source, data.fmt_desc, warns)


def z_to_s2(zmat, z0):
    """Single NxN Z matrix -> S with per-port real z0 (list)."""
    n = len(zmat)
    sq = [math.sqrt(v) for v in z0[:n]]
    w = [[zmat[i][j] / (sq[i] * sq[j]) for j in range(n)] for i in range(n)]
    im = _eye(n)
    return MATMUL(_msub(w, im), INV(_madd(w, im)))


def z_to_y(data):
    """SNPData(Z) -> SNPData(Y); singular points dropped with a warning."""
    freq, mats, warns = [], [], list(data.warnings)
    for k, m in enumerate(data.mats):
        try:
            mats.append(INV(m))
            freq.append(data.freq[k])
        except ZeroDivisionError:
            warns.append("dropped point %g Hz (singular Z->Y)" % data.freq[k])
    if not mats:
        raise SnpError("Z singular at every point; no Y output")
    return SNPData(freq, mats, "Y", data.z0, data.source, data.fmt_desc, warns)


def z2_to_abcd(z):
    """2x2 Z -> (A, B, C, D) with [V1; I1] = [[A, B], [C, D]] [V2; -I2]."""
    (z11, z12), (z21, z22) = z
    if abs(z21) < 1e-12 * max(1.0, abs(z11), abs(z22)):
        return None
    det = z11 * z22 - z12 * z21
    return z11 / z21, det / z21, 1.0 / z21, z22 / z21


# ---------------------------------------------------------------------------
# math helpers
# ---------------------------------------------------------------------------


def csqrt_pos(z):
    """Complex sqrt with the Re>0 branch (passive characteristic impedances)."""
    r = cmath.sqrt(z)
    if r.real < 0 or (r.real == 0 and r.imag < 0):
        return -r
    return r


def unwrap_1d(values):
    """Unwrap a phase sequence (radians) along a list."""
    out = list(values)
    for i in range(1, len(out)):
        d = out[i] - out[i - 1]
        while d > math.pi:
            out[i] -= 2 * math.pi
            d = out[i] - out[i - 1]
        while d < -math.pi:
            out[i] += 2 * math.pi
            d = out[i] - out[i - 1]
    return out


def fix_sign_unwrap(theta):
    """Choose the overall sign so the mean is positive, then unwrap."""
    if not theta:
        return []
    t = list(theta)
    if sum(t) / len(t) < 0:
        t = [-v for v in t]
    return unwrap_1d(t)


def first_p2n_crossing(freq, y):
    """First positive-to-non-positive zero of y(f), linearly interpolated.
    None entries are skipped. Returns the frequency or None."""
    prev = None
    for i in range(len(y)):
        if y[i] is None:
            prev = None
            continue
        if prev is not None and prev > 0 and y[i] <= 0:
            if prev == y[i]:
                return freq[i - 1]
            t = prev / (prev - y[i])
            return freq[i - 1] + t * (freq[i] - freq[i - 1])
        prev = y[i]
    return None


def crossing_at(freq, y, level):
    """First crossing of y through `level` in either direction, interpolated.
    None entries break the run (treated as gaps). Returns frequency or None."""
    prev = None
    for i in range(len(y)):
        if y[i] is None:
            prev = None
            continue
        if prev is not None:
            d0 = prev[1] - level
            d1 = y[i] - level
            if d0 == 0:
                return prev[0]
            if d0 * d1 < 0:
                t = d0 / (d0 - d1)
                return prev[0] + t * (freq[i] - prev[0])
        prev = (freq[i], y[i])
    return None


def gmax_db(s11, s12, s21, s22):
    """ADE gmax(): MAG when Rollett K>1 else MSG, in dB (10*log10).
    Returns None when |s12*s21| is numerically zero."""
    den = abs(s12) * abs(s21)
    if den < 1e-15:
        return None
    delta = s11 * s22 - s12 * s21
    k_roll = (1 - abs(s11) ** 2 - abs(s22) ** 2 + abs(delta) ** 2) / (2 * den)
    if abs(s12) < 1e-15:
        return None
    ratio = abs(s21) / abs(s12)
    g = ratio * (k_roll - math.sqrt(k_roll ** 2 - 1)) if k_roll > 1 else ratio
    if g <= 0:
        return None
    return 10 * math.log10(g)


# ---------------------------------------------------------------------------
# port reductions
# ---------------------------------------------------------------------------


def schur_ground(mats, ground_idx):
    """Eliminate 0-based port indices forced to V=0 from every matrix.
    Returns matrices over the remaining ports (original order kept)."""
    ground_idx = set(ground_idx)
    if not ground_idx:
        return [ [row[:] for row in m] for m in mats]
    n = len(mats[0])
    keep = [i for i in range(n) if i not in ground_idx]
    out = []
    for m in mats:
        zkk = [[m[i][j] for j in keep] for i in keep]
        zkg = [[m[i][j] for j in ground_idx] for i in keep]
        zgk = [[m[i][j] for j in keep] for i in ground_idx]
        zgg = [[m[i][j] for j in ground_idx] for i in ground_idx]
        try:
            zgg_inv = INV(zgg)
        except ZeroDivisionError as e:
            raise SnpError("cannot ground ports %s (singular block): %s"
                           % (sorted(p + 1 for p in ground_idx), e))
        corr = MATMUL(MATMUL(zkg, zgg_inv), zgk)
        out.append([[zkk[i][j] - corr[i][j] for j in range(len(keep))] for i in range(len(keep))])
    return out


def sign_reduce(mats, sign_vecs):
    """Build reduced matrices from per-role sign vectors.

    sign_vecs: list of dicts {port_index(0-based): +1|-1}. Role r sees the
    terminal combination V_r = sum_u d[u]*V_u, so the reduced matrix is
    M_rs = sum_u sum_v d_r[u] * d_s[v] * Z[u][v]. A differential pair maps to
    {a: +1, b: -1}; a single-ended port to {p: +1}; floating taps map to
    nothing (open, I=0).
    """
    out = []
    for m in mats:
        red = []
        for dr in sign_vecs:
            row = []
            for ds in sign_vecs:
                acc = 0j
                for u, cu in dr.items():
                    for v, cv in ds.items():
                        acc += cu * cv * m[u][v]
                row.append(acc)
            red.append(row)
        out.append(red)
    return out


# ---------------------------------------------------------------------------
# io helpers
# ---------------------------------------------------------------------------


def write_json_atomic(path, obj):
    """Write JSON atomically (tmp + os.replace), UTF-8, trailing newline."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(obj, indent=2, ensure_ascii=True) + "\n"
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def cdict(z):
    """Complex number -> JSON-friendly dict."""
    return {"re": z.real, "im": z.imag}
