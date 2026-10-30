# -*- coding: utf-8 -*-
"""numfallback.py -- pure-stdlib numeric fallback backend for the snp_* tools.

The snp_* pipeline prefers numpy (matrix inverse / product funnel through
`numpy.linalg`); when the system Python has no numpy, this module provides the
same two primitives hand-written for small complex matrices (nested lists):
complex Gaussian elimination with partial pivoting, and a plain triple-loop
matrix product. Matrices here are at most 8x8 per frequency point, so the
pure-Python path is well within interactive-time budgets; results agree with
the numpy path to floating-point round-off (asserted by the test suite).

Forced for testing via environment variable SNP_FORCE_FALLBACK=1 (handled by
the caller, scripts/snp_common.py).
"""

def inv(a):
    """Invert a small square complex matrix (nested lists) via Gauss-Jordan
    elimination with partial pivoting. Raises ZeroDivisionError when singular
    (pivot magnitude below a relative tolerance)."""
    n = len(a)
    if any(len(row) != n for row in a):
        raise ValueError("inv: non-square matrix")
    # augmented [A | I]
    m = [[complex(a[i][j]) for j in range(n)] + [1.0 if i == j else 0.0 for j in range(n)]
         for i in range(n)]
    scale = max(1.0, max(abs(v) for row in a for v in row))
    tol = 1e-12 * scale
    for col in range(n):
        piv_row = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv_row][col]) < tol:
            raise ZeroDivisionError("inv: singular matrix (pivot %.3e at column %d)"
                                    % (abs(m[piv_row][col]), col))
        if piv_row != col:
            m[col], m[piv_row] = m[piv_row], m[col]
        piv = m[col][col]
        m[col] = [v / piv for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0:
                f = m[r][col]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[col])]
    return [row[n:] for row in m]


def matmul(a, b):
    """Product of two small complex matrices (nested lists)."""
    n, p, q = len(a), len(b), len(b[0]) if b else 0
    if any(len(row) != p for row in a):
        raise ValueError("matmul: shape mismatch")
    bt = [[b[i][j] for i in range(p)] for j in range(q)]  # transpose cache
    out = []
    for i in range(n):
        ri = a[i]
        out.append([sum(u * v for u, v in zip(ri, cj)) for cj in bt])
    return out


def eye(n):
    """Identity matrix as nested lists."""
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


if __name__ == "__main__":  # pragma: no cover - manual smoke
    a = [[2 + 1j, 1], [1, 3 - 1j]]
    ainv = inv(a)
    p = matmul(a, ainv)
    err = max(abs(p[i][j] - (1.0 if i == j else 0.0)) for i in range(2) for j in range(2))
    print("identity error: %.3e" % err)
    assert err < 1e-12
    print("ok")
