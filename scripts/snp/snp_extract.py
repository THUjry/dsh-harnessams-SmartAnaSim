# -*- coding: utf-8 -*-
"""snp_extract.py -- extract device model parameters from an S-parameter file.

Input: Touchstone .s1p..sNp, Spectre psfascii sp output, or a params CSV
written by snp_read.py (auto-detected). Everything is reduced to Z-parameters
first (per-port reference impedances honored), then per --type:

  transformer (2/3/4/5/6 ports)
      L1 = Im(Z11)/w, L2 = Im(Z22)/w, Q1 = Im(Z11)/Re(Z11), Q2 likewise,
      k = Im(Z21)/sqrt(Im(Z11)*Im(Z22))   (reported signed and |k|),
      SRF1/SRF2 = first +->- zero crossings of Im(Z11)/Im(Z22),
      Gmax = ADE gmax() (MAG for Rollett K>1, else MSG) of the reduced 2-port.
      No mutual M and no turns ratio by design.
  inductor (1/2/3 ports)
      L = Im(Z)/w, Q = Im(Z)/Re(Z), SRF = first +->- zero of Im(Z).
  tl (2 ports single-ended, or 4 ports differential)
      Z->ABCD, Zc = sqrt(B/C) (Re>0 branch), gamma*l from the ABCD eigenvalue
      with |lambda|<1 (gamma*l = -ln lambda); theta unwrapped over frequency;
      optional --length-m adds eps_eff, alpha dB per unit length.
  coupled_tl (4 ports single-ended, or 8 ports differential)
      even/odd modal decomposition (T transform), Z0 = sqrt(Zd^2 - Zt^2),
      gamma*l = acosh(Zd/Zt), averaged Zd/Zt against numeric noise, theta
      unwrap + global sign fix, coupling k = (Z0e-Z0o)/(Z0e+Z0o), f(theta=90).
      theory_ok: 1 when the relative modal off-block residual
      max|Zm[even,odd]| / max|Zm| at the extraction frequency is within the
      gate (default 4x the value of the validated reference coupler -- see
      THEORY_GATE_DEFAULT below; override with --theory-gate).

Port model (single-ended means the cold terminal was already grounded in the
EM setup, so that side exposes ONE port; differential means both terminals are
ports). Default topologies by file port count (= the port order the tb/EM
scripts write):

    ports  inductor                 transformer                tl        coupled_tl
    1      single-ended (Z11)       -                          -         -
    2      coil (1,2) differential  (1)SE + (2)SE              2p SE     -
    3      coil (1,2), ct=3         coil1 (1,2) diff + coil2   -         -
                                       (3) single-ended
    4      -                        coil1 (1,2) + coil2 (3,4)  4p diff:  4p SE
                                       (CT/SE variants: give      pairs
                                       coil/ct explicitly)       (1,2)+
                                                                  (3,4)
    5      -                        (1,2)+ct1=5, (3,4)         -         -
    6      -                        (1,2)+ct1=5, (3,4)+ct2=6   -         -
    8      -                        -                          -         8p diff:
                                                                  (1,2)(3,4)
                                                                  (5,6)(7,8)

Parameters re-map the roles:
  --coil1/--coil2 (transformer), --coil (inductor): 1 or 2 file port numbers
     per side; 2 numbers = differential side, 1 number = single-ended side.
     For 4-port tl use --coil1/--coil2 as the near/far differential pairs; for
     8-port coupled_tl use --coil1..--coil4 as the four differential pairs.
  --ct1/--ct2 (transformer), --ct (inductor): file port number of the center
     tap (a tap is itself a file port; it floats unless grounded via --se).
  --se: comma list of file ports to ground during extraction (V=0, Schur
     elimination): grounding one terminal of a differential side turns that
     side into a single-ended view; listing a center tap grounds it; for a
     2-port transformer --se 2 is the shorted-secondary leakage measurement.

Port plan echo: the JSON result and stdout always carry the effective port
plan (sides, differential/single-ended, tap ports and their state, whether it
came from defaults, explicit parameters, or auto adjustment). Confirm the plan
before trusting the numbers.

Auto port adjustment: when coil/ct parameters are absent, or when the chosen
plan fails the physicality checks (L<=0, |k|>1.05, Re(Z0)<=0, non-increasing
theta, Zc/Z0 outside 1..1000 ohm, alpha<0), candidate mappings are enumerated
(auto search: winding swaps, tap positions, differential/single-ended roles,
all 24 role permutations for coupled_tl; scoring on a decimated frequency
grid, coupled_tl scored by relative off-block residual + theta monotonicity,
transformer/inductor ranked by L1+L2 / L after passing) and the best plan is
adopted, with every scored candidate reported in the JSON.

Usage:
    python snp_extract.py --file <path> --type <tl|coupled_tl|transformer|inductor>
        [--freq-hz F] [--coil1 1,2] [--coil2 3,4] [--coil 1,2] [--ct1 5] [--ct2 6]
        [--ct 3] [--se 2,4] [--length-m L] [--theory-gate G] [--out-dir DIR]

Exit codes: 0 success, 1 extraction produced unphysical results (JSON still
written), 2 input/usage error.
"""

import argparse
import cmath
import itertools
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snp_common as sc  # noqa: E402

SQRT1_2 = 1.0 / math.sqrt(2.0)
T_MODAL = [[SQRT1_2 * v for v in row] for row in
           [[1, 1, 0, 0], [0, 0, 1, 1], [1, -1, 0, 0], [0, 0, 1, -1]]]
T_MODAL_T = [[T_MODAL[j][i] for j in range(4)] for i in range(4)]
# Default theory gate: 4x the relative off-block residual at 94 GHz of the
# validated reference coupler (the Lange hybrid whose port mapping, Z0e/Z0o
# and key results are regression-tested in tests/data). Calibration run
# 2026-10-30 against its reference key_results.txt: off-block 1.46 ohm at
# 94 GHz on max|Zm| ~ 116 ohm -> rel 1.255e-2; gate = 4x = 5.0e-2.
# Override with --theory-gate.
THEORY_GATE_DEFAULT = 5.0e-2

ZC_MIN, ZC_MAX = 1.0, 1000.0


class ExtractError(Exception):
    """Maps to exit code 2 (bad input/plan)."""


# ---------------------------------------------------------------------------
# plan construction
# ---------------------------------------------------------------------------


def parse_ports_spec(name, value):
    """'1,2' -> [1,2] (1-based, 1 or 2 entries)."""
    try:
        ports = [int(v.strip()) for v in str(value).split(",") if v.strip()]
    except ValueError:
        raise ExtractError("--%s: cannot parse port list %r" % (name, value))
    if not ports or len(ports) > 2 or len(set(ports)) != len(ports):
        raise ExtractError("--%s: expected one or two distinct ports, got %r" % (name, value))
    return ports


def parse_int_arg(name, value):
    try:
        return int(str(value).strip())
    except ValueError:
        raise ExtractError("--%s: expected an integer port number, got %r" % (name, value))


def parse_se_spec(value):
    try:
        return sorted({int(v.strip()) for v in str(value).split(",") if v.strip()})
    except ValueError:
        raise ExtractError("--se: cannot parse port list %r" % value)


def _mk_role(name, ports, ct=None):
    return {"name": name, "ports": list(ports), "ct": ct, "ct_state": None}


def build_default_plan(dev, n, se):
    """Default topology from the port-count table (write-script convention)."""
    if dev == "inductor":
        if n == 1:
            roles = [_mk_role("coil", [1])]
        elif n == 2:
            roles = [_mk_role("coil", [1, 2])]
        elif n == 3:
            roles = [_mk_role("coil", [1, 2], ct=3)]
        else:
            raise ExtractError("inductor supports 1/2/3-port files, got %d" % n)
    elif dev == "transformer":
        if n == 2:
            roles = [_mk_role("primary", [1]), _mk_role("secondary", [2])]
        elif n == 3:
            roles = [_mk_role("primary", [1, 2]), _mk_role("secondary", [3])]
        elif n == 4:
            roles = [_mk_role("primary", [1, 2]), _mk_role("secondary", [3, 4])]
        elif n == 5:
            roles = [_mk_role("primary", [1, 2], ct=5), _mk_role("secondary", [3, 4])]
        elif n == 6:
            roles = [_mk_role("primary", [1, 2], ct=5), _mk_role("secondary", [3, 4], ct=6)]
        else:
            raise ExtractError("transformer supports 2/3/4/5/6-port files, got %d" % n)
    elif dev == "tl":
        if n == 2:
            roles = [_mk_role("in", [1]), _mk_role("out", [2])]
        elif n == 4:
            roles = [_mk_role("in", [1, 2]), _mk_role("out", [3, 4])]
        else:
            raise ExtractError("tl supports 2/4-port files, got %d" % n)
    elif dev == "coupled_tl":
        if n == 4:
            roles = [_mk_role(k, [p]) for k, p in
                     zip(("leftA", "leftB", "rightA", "rightB"), (1, 2, 3, 4))]
        elif n == 8:
            roles = [_mk_role(k, pr) for k, pr in zip(
                ("leftA", "leftB", "rightA", "rightB"),
                ([1, 2], [3, 4], [5, 6], [7, 8]))]
        else:
            raise ExtractError("coupled_tl supports 4/8-port files, got %d" % n)
    else:
        raise ExtractError("unknown --type %r" % dev)
    return {"device": dev, "roles": roles, "se": list(se), "origin": "default"}


def apply_explicit(plan, args):
    """Overlay explicit --coil*/--ct* parameters onto the default plan."""
    dev = plan["device"]
    given = {}
    if dev == "transformer":
        if args.coil1:
            given["primary"] = args.coil1
        if args.coil2:
            given["secondary"] = args.coil2
        ct1 = parse_int_arg("ct1", args.ct1) if args.ct1 else None
        ct2 = parse_int_arg("ct2", args.ct2) if args.ct2 else None
        if ct1 is not None or ct2 is not None:
            given["ct"] = (ct1, ct2)
    elif dev == "inductor":
        if args.coil:
            given["coil"] = args.coil
        if args.ct:
            given["ct"] = parse_int_arg("ct", args.ct)
    elif dev == "tl":
        if args.coil1:
            given["in"] = args.coil1
        if args.coil2:
            given["out"] = args.coil2
    elif dev == "coupled_tl":
        names = ("leftA", "leftB", "rightA", "rightB")
        for name, val in zip(names, (args.coil1, args.coil2, args.coil3, args.coil4)):
            if val:
                given[name] = val
    if not given:
        return plan
    plan = {"device": dev, "roles": [dict(r) for r in plan["roles"]], "se": list(plan["se"]),
            "origin": "explicit"}
    by_name = {r["name"]: r for r in plan["roles"]}
    # port (re)mapping first ...
    ct_spec = given.pop("ct", None)
    for name, ports in given.items():
        by_name[name]["ports"] = list(ports)
    # ... then the tap assignments
    if ct_spec is not None and dev == "transformer":
        ct1, ct2 = ct_spec
        if ct1 is not None:
            by_name["primary"]["ct"] = ct1
        if ct2 is not None:
            by_name["secondary"]["ct"] = ct2
    elif ct_spec is not None and dev == "inductor":
        by_name["coil"]["ct"] = ct_spec
    return plan


def check_plan(plan, n, se_set):
    """Validate port numbers, sizes and side sizes after grounding."""
    dev = plan["device"]
    used = []
    for r in plan["roles"]:
        for p in r["ports"] + ([r["ct"]] if r["ct"] else []):
            if not (1 <= p <= n):
                raise ExtractError("role %r references port %d outside 1..%d" % (r["name"], p, n))
        used.extend(r["ports"])
        if r["ct"]:
            used.append(r["ct"])
    if len(used) != len(set(used)):
        raise ExtractError("plan reuses a port across roles: %s" % plan_roles_str(plan))
    for p in se_set:
        if not (1 <= p <= n):
            raise ExtractError("--se references port %d outside 1..%d" % (p, n))
    # effective side sizes after grounding: a side with all terminals
    # grounded is "shorted" (drops out of the reduction -- the classic
    # shorted-secondary leakage measurement keeps only the other side)
    for r in plan["roles"]:
        live = [p for p in r["ports"] if p not in se_set]
        r["live"] = live
        r["shorted"] = not live
        if r["ct"]:
            r["ct_state"] = "grounded" if r["ct"] in se_set else "floating"
    if not any(r["live"] for r in plan["roles"]):
        raise ExtractError("every role is fully grounded by --se")
    if dev == "transformer" and len(plan["roles"]) != 2:
        raise ExtractError("transformer needs exactly two sides")
    if dev == "inductor" and plan["roles"][0]["shorted"]:
        raise ExtractError("coil fully grounded by --se")
    if dev == "tl" and any(r["shorted"] for r in plan["roles"]):
        raise ExtractError("tl in/out roles cannot be fully grounded")
    if dev == "coupled_tl":
        if len(plan["roles"]) != 4:
            raise ExtractError("coupled_tl needs four roles")
        sizes = {len(r["live"]) for r in plan["roles"]}
        if len(sizes) != 1 or 0 in sizes:
            raise ExtractError("coupled_tl roles must mix to a 4x4 matrix "
                               "(same terminal count per role)")
    return plan


def plan_roles_str(plan):
    parts = []
    for r in plan["roles"]:
        if r.get("shorted"):
            mode = "shorted"
        else:
            mode = "differential" if len(r.get("live", r["ports"])) == 2 else "single-ended"
        s = "%s: ports %s (%s)" % (r["name"], ",".join(str(p) for p in r["ports"]), mode)
        if r["ct"]:
            s += ", ct=%d (%s)" % (r["ct"], r.get("ct_state") or "floating")
        parts.append(s)
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# candidate enumeration (auto port adjustment)
# ---------------------------------------------------------------------------


def _pair_partitions(items):
    """All ways to split items into an ordered pair of unordered pairs."""
    out = []
    for a in itertools.combinations(items, 2):
        rest = [x for x in items if x not in a]
        for b in itertools.combinations(rest, 2):
            if set((frozenset(a), frozenset(b))) not in [set((frozenset(p), frozenset(q)))
                                                          for p, q in out]:
                out.append((a, b))
    return out


def enumerate_candidates(dev, n, se_set):
    """Candidate plans for the auto search (defaults included)."""
    free = [p for p in range(1, n + 1) if p not in se_set]
    plans = []
    if dev in ("inductor", "transformer"):
        sizes = {"inductor": (1, 0), "transformer": (2, 2)}[dev]
        if dev == "transformer":
            if n == 2:
                combos = [([1], [2]), ([2], [1])]
            elif n == 3:
                combos = [((1, 2), (3,)), ((2, 3), (1,)), ((1, 3), (2,)),
                          ((3,), (1, 2)), ((1,), (2, 3)), ((2,), (1, 3))]
            else:
                combos = []
                for a, b in _pair_partitions(free):
                    combos.append((list(a), list(b)))
                    combos.append((list(b), list(a)))
            names = ("primary", "secondary")
        else:
            if n == 1:
                combos = [([1], [])]
            elif n == 2:
                combos = [([1, 2], []), ([1], []), ([2], [])]
            else:
                combos = [([1, 2], []), ([2, 3], []), ([1, 3], [])] + \
                         [([p], []) for p in free]
            names = ("coil", None)
        for c in combos:
            side1, side2 = c
            if dev == "transformer":
                roles = [_mk_role(names[0], side1), _mk_role(names[1], side2)]
                cts = [p for p in free if p not in side1 and p not in side2]
                if len(cts) == 1:
                    variants = [(cts[0], None), (None, cts[0]), (None, None)]
                elif len(cts) == 2:
                    variants = [(cts[0], cts[1]), (cts[1], cts[0])]
                else:
                    variants = [(None, None)]
                for ct1, ct2 in variants:
                    roles[0]["ct"] = ct1
                    roles[1]["ct"] = ct2
                    plans.append({"device": dev, "roles": [dict(r) for r in roles],
                                  "se": sorted(se_set), "origin": "auto"})
            else:
                roles = [_mk_role("coil", side1)]
                cts = [p for p in free if p not in side1]
                cts = [p for p in cts if len(side1) == 2]  # tap only for a 2-terminal coil
                variants = cts + [None] if cts else [None]
                for ct in variants:
                    roles[0]["ct"] = ct
                    plans.append({"device": dev, "roles": [dict(r) for r in roles],
                                  "se": sorted(se_set), "origin": "auto"})
    elif dev == "tl":
        if n == 2:
            plans.append({"device": dev, "roles": [_mk_role("in", [1]), _mk_role("out", [2])],
                          "se": sorted(se_set), "origin": "auto"})
        else:
            for a, b in _pair_partitions(free):
                plans.append({"device": dev,
                              "roles": [_mk_role("in", list(a)), _mk_role("out", list(b))],
                              "se": sorted(se_set), "origin": "auto"})
    elif dev == "coupled_tl":
        names = ("leftA", "leftB", "rightA", "rightB")
        if n == 4:
            for perm in itertools.permutations(free, 4):
                plans.append({"device": dev,
                              "roles": [_mk_role(nm, [p]) for nm, p in zip(names, perm)],
                              "se": sorted(se_set), "origin": "auto"})
        else:
            families = [
                [(1, 2), (3, 4), (5, 6), (7, 8)],
                [(1, 3), (2, 4), (5, 7), (6, 8)],
                [(1, 5), (2, 6), (3, 7), (4, 8)],
            ]
            for fam in families:
                for perm in itertools.permutations(range(4)):
                    roles = [_mk_role(names[k], list(fam[perm[k]])) for k in range(4)]
                    plans.append({"device": dev, "roles": roles,
                                  "se": sorted(se_set), "origin": "auto"})
    return plans


# ---------------------------------------------------------------------------
# reduction
# ---------------------------------------------------------------------------


def reduce_plan(mats, plan, n):
    """Apply --se grounding then the per-role sign vectors.
    Returns (reduced_mats, sign_vectors) with 0-based indices."""
    se_idx = [p - 1 for p in plan["se"]]
    grounded = schur_safe(mats, se_idx)
    old2new = {}
    new = 0
    for p in range(n):
        if p not in se_idx:
            old2new[p] = new
            new += 1
    svecs = []
    for r in plan["roles"]:
        if r.get("shorted"):
            continue  # fully grounded side: already eliminated by the Schur step
        live = r.get("live") or [p for p in r["ports"] if p not in plan["se"]]
        if len(live) == 2:
            svecs.append({old2new[live[0] - 1]: 1.0, old2new[live[1] - 1]: -1.0})
        else:
            svecs.append({old2new[live[0] - 1]: 1.0})
    return sc.sign_reduce(grounded, svecs), svecs


def schur_safe(mats, se_idx):
    if not se_idx:
        return mats
    try:
        return sc.schur_ground(mats, se_idx)
    except sc.SnpError:
        # a fully-degenerate ground block: drop those ports instead
        keep = [i for i in range(len(mats[0])) if i not in se_idx]
        return [[[m[i][j] for j in keep] for i in keep] for m in mats]


# ---------------------------------------------------------------------------
# device math
# ---------------------------------------------------------------------------


def _role_z0(plan, zref):
    """Reference impedance per reduced role (differential port = 2*zref);
    shorted roles contribute no reduced port and are skipped."""
    out = []
    for r in plan["roles"]:
        if r.get("shorted"):
            continue
        live = r.get("live") or r["ports"]
        out.append(2.0 * zref if len(live) == 2 else zref)
    return out


def xfmr_curves(freq, mats2, z0_roles, single_side=None):
    """Transformer curves from the reduced Z; entries may be None per point.

    mats2 is 2x2 for the full transformer, or 1x1 when one side is fully
    grounded (shorted-secondary leakage view) -- then single_side names the
    surviving role ("primary"/"secondary") so values land in the right slots.
    """
    n = len(freq)
    cur = {k: [None] * n for k in
           ("L1", "L2", "Q1", "Q2", "k", "gmax_db", "imZ11", "imZ22")}
    for idx, m in enumerate(mats2):
        w = 2.0 * math.pi * freq[idx]
        if len(m) == 1:
            z = m[0][0]
            lkey = "L1" if single_side == "primary" else "L2"
            qkey = "Q1" if single_side == "primary" else "Q2"
            ikey = "imZ11" if single_side == "primary" else "imZ22"
            cur[ikey][idx] = z.imag
            if w > 0:
                cur[lkey][idx] = z.imag / w
            if z.real != 0:
                cur[qkey][idx] = z.imag / z.real
            continue
        z11, z22, z21 = m[0][0], m[1][1], m[1][0]
        cur["imZ11"][idx] = z11.imag
        cur["imZ22"][idx] = z22.imag
        if w > 0:
            cur["L1"][idx] = z11.imag / w
            cur["L2"][idx] = z22.imag / w
        if z11.real != 0:
            cur["Q1"][idx] = z11.imag / z11.real
        if z22.real != 0:
            cur["Q2"][idx] = z22.imag / z22.real
        d = z11.imag * z22.imag
        if d > 0:
            cur["k"][idx] = z21.imag / math.sqrt(d)
        try:
            s = sc.z_to_s2(m, z0_roles)
            cur["gmax_db"][idx] = sc.gmax_db(s[0][0], s[0][1], s[1][0], s[1][1])
        except ZeroDivisionError:
            pass
    return cur


def ind_curves(freq, mats1):
    n = len(freq)
    cur = {"L": [None] * n, "Q": [None] * n, "imZ": [None] * n}
    for idx, m in enumerate(mats1):
        w = 2.0 * math.pi * freq[idx]
        z = m[0][0]
        cur["imZ"][idx] = z.imag
        if w > 0:
            cur["L"][idx] = z.imag / w
        if z.real != 0:
            cur["Q"][idx] = z.imag / z.real
    return cur


def tl_curves(freq, mats2):
    n = len(freq)
    cur = {k: [None] * n for k in ("Zc", "gamma_l")}
    prev1 = None  # unwrapped theta of the previous valid point
    prev2 = None  # ... and the one before it
    for idx, m in enumerate(mats2):
        abcd = sc.z2_to_abcd(m)
        if abcd is None:
            continue
        a, b, c, d = abcd
        if c == 0:
            continue
        try:
            zc = sc.csqrt_pos(b / c)
            disc = cmath.sqrt(((a - d) / 2.0) ** 2 + b * c)
            half = (a + d) / 2.0
            cands = [-cmath.log(lam) for lam in (half + disc, half - disc) if lam != 0]
            if not cands:
                continue
            # passive lines keep alpha >= 0; for exactly lossless lines the two
            # eigenvalues are conjugates (magnitude tie) and the branch must be
            # picked by phase continuity -- linear extrapolation from the two
            # previous points disambiguates the pi-wrap where plain continuity
            # would follow the mirror branch
            passive = [g for g in cands if g.real >= -1e-9] or cands
            if prev1 is None:
                # first point: theta starts near 0+, prefer the small positive
                gl = min(passive, key=lambda g: (abs(g.imag), -g.imag))
            else:
                pred = prev1 + (prev1 - prev2) if prev2 is not None else prev1
                def _dist(g):
                    norm = g.imag + 2.0 * math.pi * round((pred - g.imag) / (2.0 * math.pi))
                    return (abs(norm - pred), -g.imag)
                gl = min(passive, key=_dist)
            prev2 = prev1
            prev1 = gl.imag
            cur["Zc"][idx] = zc
            cur["gamma_l"][idx] = gl
        except (ZeroDivisionError, ValueError):
            continue
    thetas = [g.imag if g else None for g in cur["gamma_l"]]
    theta = sc.fix_sign_unwrap([t for t in thetas if t is not None])
    ti = 0
    for idx in range(n):
        if thetas[idx] is not None:
            thetas[idx] = theta[ti]
            ti += 1
    cur["theta"] = thetas
    return cur


def ctl_curves(freq, mats4):
    """Even/odd modal extraction on reduced 4x4 Z (HybridAnalysis math)."""
    n = len(freq)
    keys = ("Z0e", "Z0o", "gamma_le", "gamma_lo", "rel_off")
    cur = {k: [None] * n for k in keys}
    for idx, m in enumerate(mats4):
        zm = sc.MATMUL(sc.MATMUL(T_MODAL, m), T_MODAL_T)
        zmax = max(abs(zm[i][j]) for i in range(4) for j in range(4))
        off = max(max(abs(zm[i][j]) for i in range(0, 2) for j in range(2, 4)),
                  max(abs(zm[i][j]) for i in range(2, 4) for j in range(0, 2)))
        cur["rel_off"][idx] = (off / zmax) if zmax > 0 else None
        zde = 0.5 * (zm[0][0] + zm[1][1])
        zte = 0.5 * (zm[0][1] + zm[1][0])
        zdo = 0.5 * (zm[2][2] + zm[3][3])
        zto = 0.5 * (zm[2][3] + zm[3][2])
        try:
            cur["Z0e"][idx] = sc.csqrt_pos(zde ** 2 - zte ** 2)
            cur["Z0o"][idx] = sc.csqrt_pos(zdo ** 2 - zto ** 2)
            if zte != 0:
                cur["gamma_le"][idx] = cmath.acosh(zde / zte)
            if zto != 0:
                cur["gamma_lo"][idx] = cmath.acosh(zdo / zto)
        except (ZeroDivisionError, ValueError):
            continue
    for gk, tk in (("gamma_le", "theta_e"), ("gamma_lo", "theta_o")):
        raw = [g.imag if g else None for g in cur[gk]]
        unw = sc.fix_sign_unwrap([t for t in raw if t is not None])
        ti = 0
        out = [None] * n
        for idx in range(n):
            if raw[idx] is not None:
                out[idx] = unw[ti]
                ti += 1
        cur[tk] = out
    return cur


# ---------------------------------------------------------------------------
# physicality + scoring
# ---------------------------------------------------------------------------


def _majority_positive(series, upto):
    vals = [v for v in series[: upto + 1] if v is not None]
    if not vals:
        return False
    return sum(1 for v in vals if v > 0) >= 0.7 * len(vals)


def physicality(dev, cur, i_ex):
    """Return (ok, reasons, rank_key) evaluated on decimated curves."""
    reasons = []
    if dev == "transformer":
        has1 = any(v is not None for v in cur["L1"])
        has2 = any(v is not None for v in cur["L2"])
        l1 = cur["L1"][i_ex] if has1 else None
        l2 = cur["L2"][i_ex] if has2 else None
        k = cur["k"][i_ex]
        if has1 and (l1 is None or l1 <= 0):
            reasons.append("L1<=0 at extraction freq")
        if has2 and (l2 is None or l2 <= 0):
            reasons.append("L2<=0 at extraction freq")
        if k is None and has1 and has2:
            reasons.append("k undefined at extraction freq")
        elif k is not None and abs(k) > 1.05:
            reasons.append("|k|>1.05 (%.3f)" % abs(k))
        if has1 and not _majority_positive(cur["L1"], i_ex):
            reasons.append("L1 not majority-positive below f_ex")
        if has2 and not _majority_positive(cur["L2"], i_ex):
            reasons.append("L2 not majority-positive below f_ex")
        lsum = (l1 or 0) + (l2 or 0)
        return (not reasons), reasons, lsum
    if dev == "inductor":
        l, q = cur["L"][i_ex], cur["Q"][i_ex]
        if l is None or l <= 0:
            reasons.append("L<=0 at extraction freq")
        if q is None or q <= 0:
            reasons.append("Q<=0 at extraction freq")
        if not _majority_positive(cur["L"], i_ex):
            reasons.append("L not majority-positive below f_ex")
        return (not reasons), reasons, (l or 0)
    if dev == "tl":
        zc, th = cur["Zc"][i_ex], cur["theta"][i_ex]
        if zc is None:
            reasons.append("Zc undefined at extraction freq")
        elif not (ZC_MIN <= zc.real <= ZC_MAX):
            reasons.append("Re(Zc)=%.2g outside %.0f..%.0f ohm" % (zc.real, ZC_MIN, ZC_MAX))
        elif abs(zc.imag) > zc.real:
            reasons.append("Zc not resistive-dominant (%.3g%+.3gj)"
                           % (zc.real, zc.imag))
        th_vals = [t for t in cur["theta"] if t is not None]
        if th is None or th <= 1e-9:
            reasons.append("theta undefined/zero at extraction freq")
        elif len(th_vals) >= 3:
            if th_vals[-1] <= th_vals[0]:
                reasons.append("theta shows no net increase over the band")
            dec = sum(1 for a, b in zip(th_vals, th_vals[1:]) if b < a - 1e-9)
            if dec > 0.15 * (len(th_vals) - 1):
                reasons.append("theta not increasing (%d/%d steps)" % (dec, len(th_vals) - 1))
        alphas = [g.real for g in cur["gamma_l"] if g]
        for a in alphas:
            if a < -1e-6 * (1.0 + abs(a)):
                reasons.append("negative alpha (active line)")
                break
        return (not reasons), reasons, 0.0
    if dev == "coupled_tl":
        z0e, z0o = cur["Z0e"][i_ex], cur["Z0o"][i_ex]
        if z0e is None or z0o is None:
            reasons.append("modal extraction undefined at f_ex")
        else:
            for nm, z in (("Z0e", z0e), ("Z0o", z0o)):
                if z.real <= 0:
                    reasons.append("Re(%s)<=0" % nm)
                elif not (ZC_MIN <= z.real <= ZC_MAX):
                    reasons.append("Re(%s)=%.2g outside %.0f..%.0f ohm"
                                   % (nm, z.real, ZC_MIN, ZC_MAX))
                elif abs(z.imag) > z.real:
                    # a passive line's modal impedance is resistive-dominant;
                    # near-pure-imaginary values mean the grouping block-
                    # diagonalizes into non-physical modes (measured trap:
                    # through-pair grouping on coupler test benches)
                    reasons.append("%s not resistive-dominant (%.3g%+.3gj)"
                                   % (nm, z.real, z.imag))
        for tk in ("theta_e", "theta_o"):
            th_ex = cur[tk][i_ex]
            if th_ex is None or th_ex <= 0:
                reasons.append("%s<=0 at f_ex" % tk)
        th_e, th_o = cur["theta_e"][i_ex], cur["theta_o"][i_ex]
        if th_e is not None and th_o is not None and th_e > 0 and th_o > 0:
            # one physical section -> both modes traverse the same length;
            # a large theta_e/theta_o spread betrays a through-pair grouping
            # (that trap block-diagonalizes perfectly but splits the line into
            # two unrelated electrical lengths)
            spread = abs(th_e - th_o) / (th_e + th_o)
            if spread > 0.25:
                reasons.append("theta_e/theta_o spread %.0f%% (>25%% of sum)"
                               % (100.0 * spread))
        mono_bad = 0
        mono_tot = 0
        for tk in ("theta_e", "theta_o"):
            vals = [t for t in cur[tk] if t is not None]
            if len(vals) >= 3:
                mono_tot += len(vals) - 1
                mono_bad += sum(1 for a, b in zip(vals, vals[1:]) if b < a - 1e-9)
        if mono_tot and mono_bad > 0.2 * mono_tot:
            reasons.append("theta not monotonic (%d/%d steps)" % (mono_bad, mono_tot))
        rel_off_band = max([v for v in cur["rel_off"] if v is not None] or [1.0])
        mono_frac = (mono_bad / mono_tot) if mono_tot else 0.0
        score = rel_off_band + 2.0 * mono_frac
        return (not reasons), reasons, -score
    raise ExtractError("unknown device %r" % dev)


# ---------------------------------------------------------------------------
# main pipeline
# ---------------------------------------------------------------------------


def decimate(n):
    if n <= 200:
        return list(range(n))
    step = n / 200.0
    idx = sorted({min(n - 1, int(k * step)) for k in range(200)} | {n - 1})
    return idx


def pick_fex_index(freq, freq_hz):
    pos = [f for f in freq if f > 0]
    if not pos:
        raise ExtractError("no positive frequency points")
    if freq_hz is None:
        lo, hi = min(pos), max(pos)
        target = math.sqrt(lo * hi)
    else:
        target = float(freq_hz)
    return min(range(len(freq)), key=lambda i: abs(freq[i] - target))


def eval_plan(zd, plan, freq_sel, i_ex_full, zref):
    """Reduce + compute curves for one plan on the (possibly decimated) grid.
    Returns dict with curves and physicality verdict."""
    n = zd.n_ports
    try:
        check_plan(plan, n, set(plan["se"]))
    except ExtractError as e:
        return {"ok": False, "reasons": [str(e)], "rank": -1e18, "plan": plan}
    dev = plan["device"]
    mats_sel = [zd.mats[i] for i in freq_sel]
    freq_sel_v = [zd.freq[i] for i in freq_sel]
    i_ex = min(range(len(freq_sel_v)), key=lambda i: abs(freq_sel_v[i] - zd.freq[i_ex_full]))
    try:
        red, _svecs = reduce_plan(mats_sel, plan, n)
    except (ExtractError, sc.SnpError) as e:
        return {"ok": False, "reasons": [str(e)], "rank": -1e18, "plan": plan}
    if dev == "transformer":
        live_roles = [r["name"] for r in plan["roles"] if not r.get("shorted")]
        single = live_roles[0] if len(live_roles) == 1 else None
        cur = xfmr_curves(freq_sel_v, red, _role_z0(plan, zref), single_side=single)
    elif dev == "inductor":
        cur = ind_curves(freq_sel_v, red)
    elif dev == "tl":
        cur = tl_curves(freq_sel_v, red)
    else:
        cur = ctl_curves(freq_sel_v, red)
    ok, reasons, rank = physicality(dev, cur, i_ex)
    return {"ok": ok, "reasons": reasons, "rank": rank, "plan": plan,
            "curves": cur, "i_ex": i_ex, "freq_sel": freq_sel_v}


def main(argv=None):
    ap = argparse.ArgumentParser(description="extract device parameters from S-parameter data")
    ap.add_argument("--file", required=True)
    ap.add_argument("--type", required=True,
                    choices=["tl", "coupled_tl", "transformer", "inductor"])
    ap.add_argument("--freq-hz", type=float, default=None,
                    help="extraction frequency in Hz (default: band geometric midpoint)")
    ap.add_argument("--coil1", default=None, help="side-1 / near pair ports, e.g. 1,2")
    ap.add_argument("--coil2", default=None, help="side-2 / far pair ports, e.g. 3,4")
    ap.add_argument("--coil3", default=None, help="coupled_tl 8-port: third pair")
    ap.add_argument("--coil4", default=None, help="coupled_tl 8-port: fourth pair")
    ap.add_argument("--coil", default=None, help="inductor coil ports (1 or 2)")
    ap.add_argument("--ct1", default=None, help="transformer primary tap port")
    ap.add_argument("--ct2", default=None, help="transformer secondary tap port")
    ap.add_argument("--ct", default=None, help="inductor tap port")
    ap.add_argument("--se", default=None,
                    help="comma list of ports to ground during extraction (V=0)")
    ap.add_argument("--length-m", type=float, default=None,
                    help="physical length in meters (enables eps_eff and alpha per length)")
    ap.add_argument("--theory-gate", type=float, default=None,
                    help="relative off-block residual gate for coupled_tl theory_ok")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)

    se = parse_se_spec(args.se) if args.se else []
    for opt in ("coil1", "coil2", "coil3", "coil4", "coil"):
        setattr(args, opt, parse_ports_spec(opt, getattr(args, opt)) if getattr(args, opt) else None)

    try:
        data = sc.load_any(args.file)
        zd = sc.to_z(data)
    except sc.SnpError as e:
        print("[snp] error: %s" % e, file=sys.stderr)
        return 2
    n = zd.n_ports
    zref = zd.z0[0] if zd.z0 and len(set(round(v, 6) for v in zd.z0[:n])) == 1 else 50.0
    dev = args.type

    try:
        plan0 = build_default_plan(dev, n, se)
        plan0 = apply_explicit(plan0, args)
        check_plan(plan0, n, set(se))
    except ExtractError as e:
        print("[snp] error: %s" % e, file=sys.stderr)
        return 2

    try:
        i_ex_full = pick_fex_index(zd.freq, args.freq_hz)
    except ExtractError as e:
        print("[snp] error: %s" % e, file=sys.stderr)
        return 2
    freq_sel = decimate(len(zd.freq))

    eval0 = eval_plan(zd, plan0, freq_sel, i_ex_full, zref)
    cand_reports = []
    chosen = None
    if eval0["ok"]:
        chosen = eval0
        cand_reports.append(_cand_report(eval0, chosen=True))
    else:
        # auto port adjustment: enumerate candidates, score on the decimated grid
        cands = enumerate_candidates(dev, n, set(se))
        best = None
        for cand in cands:
            ev = eval_plan(zd, cand, freq_sel, i_ex_full, zref)
            if ev["ok"] and (best is None or ev["rank"] > best["rank"]):
                best = ev
            cand_reports.append(_cand_report(ev, chosen=False))
        cand_reports.sort(key=lambda r: (not r["chosen"], -(r["rank"] or -1e18)))
        if best is None:
            chosen = eval0  # keep the requested/default plan; report failure
        else:
            chosen = best

    plan = chosen["plan"]
    # full-grid recompute for the winning plan
    full = eval_plan(zd, plan, list(range(len(zd.freq))), i_ex_full, zref)
    cur, i_ex = full["curves"], full["i_ex"]
    freq = zd.freq
    f_ex = freq[i_ex]
    w_ex = 2.0 * math.pi * f_ex

    result = {
        "ok": chosen["ok"],
        "device": dev,
        "source": str(Path(args.file).resolve()),
        "format": data.fmt_desc,
        "backend": sc.BACKEND,
        "freq_hz": f_ex,
        "freq_index": i_ex,
        "n_ports": n,
        "zref_ohm": zref,
        "port_plan": {
            "origin": plan["origin"],
            "auto_adjusted": plan["origin"] == "auto",
            "roles": [
                {
                    "name": r["name"],
                    "ports": r["ports"],
                    "mode": ("shorted" if r.get("shorted") else
                             ("differential" if len(r.get("live", r["ports"])) == 2
                              else "single-ended")),
                    "ct": r["ct"],
                    "ct_state": r.get("ct_state"),
                } for r in plan["roles"]
            ],
            "se_ground": plan["se"],
            "summary": plan_roles_str(plan),
        },
        "initial_plan_failed": None if eval0["ok"] else {
            "origin": eval0["plan"]["origin"],
            "summary": plan_roles_str(eval0["plan"]),
            "reasons": eval0["reasons"],
        },
        "candidates": cand_reports[:8],
        "warnings": sorted(set(zd.warnings)),
    }

    unphysical = not chosen["ok"]
    if unphysical:
        result["physicality"] = {"ok": False, "reasons": chosen["reasons"]}
        for w in chosen["reasons"]:
            print("[snp] warning: physicality: %s" % w)

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.file).resolve().parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print("[snp] error: cannot create out dir: %s" % e, file=sys.stderr)
        return 2
    stem = Path(args.file).stem
    if re.search(r"_extract_[a-z_]+$", stem):
        stem = re.sub(r"_extract_[a-z_]+$", "", stem)
    json_path = out_dir / ("%s_extract_%s.json" % (stem, dev))
    csv_path = out_dir / ("%s_extract_%s.csv" % (stem, dev))

    print("[snp] source: %s (%s, backend=%s)" % (result["source"], data.fmt_desc, sc.BACKEND))
    print("[snp] port plan (%s): %s" % (plan["origin"], plan_roles_str(plan)))
    if result["initial_plan_failed"]:
        print("[snp] initial plan (%s) failed: %s"
              % (result["initial_plan_failed"]["origin"],
                 "; ".join(result["initial_plan_failed"]["reasons"])))
        if plan["origin"] == "auto":
            print("[snp] auto adjustment adopted the plan above")
    print("[snp] extraction frequency %.6g Hz (point %d of %d)" % (f_ex, i_ex + 1, len(freq)))

    summary = {}
    if dev == "transformer":
        summary = {
            "L1_H": cur["L1"][i_ex], "L2_H": cur["L2"][i_ex],
            "Q1": cur["Q1"][i_ex], "Q2": cur["Q2"][i_ex],
            "k": cur["k"][i_ex],
            "k_abs": abs(cur["k"][i_ex]) if cur["k"][i_ex] is not None else None,
            "gmax_db": cur["gmax_db"][i_ex],
            "srf1_hz": sc.first_p2n_crossing(freq, cur["imZ11"]),
            "srf2_hz": sc.first_p2n_crossing(freq, cur["imZ22"]),
        }
        _write_curve_csv(csv_path, freq, {
            "L1_H": cur["L1"], "L2_H": cur["L2"], "k": cur["k"],
            "Q1": cur["Q1"], "Q2": cur["Q2"], "gmax_db": cur["gmax_db"],
        })
        _print_summary(summary, [("L1", "L1_H", "H"), ("L2", "L2_H", "H"),
                                 ("Q1", "Q1", ""), ("Q2", "Q2", ""),
                                 ("k", "k", ""), ("gmax", "gmax_db", "dB")])
        _print_srf(summary, ("srf1_hz", "srf2_hz"))
    elif dev == "inductor":
        summary = {
            "L_H": cur["L"][i_ex], "Q": cur["Q"][i_ex],
            "srf_hz": sc.first_p2n_crossing(freq, cur["imZ"]),
        }
        _write_curve_csv(csv_path, freq, {"L_H": cur["L"], "Q": cur["Q"]})
        _print_summary(summary, [("L", "L_H", "H"), ("Q", "Q", "")])
        _print_srf(summary, ("srf_hz",))
    elif dev == "tl":
        zc = cur["Zc"][i_ex]
        gl = cur["gamma_l"][i_ex]
        th_ex = cur["theta"][i_ex]
        summary = {"Zc_ohm": sc.cdict(zc) if zc else None,
                   "gamma_l": sc.cdict(gl) if gl else None,
                   "theta_deg": math.degrees(th_ex) if th_ex is not None else None}
        if args.length_m and gl and th_ex is not None:
            length = args.length_m
            beta = th_ex / length
            eps_eff = (beta * 299792458.0 / w_ex) ** 2
            summary["eps_eff"] = eps_eff
            summary["alpha_db_per_m"] = 8.686 * gl.real / length
        rows = {"Zc_re": [v.real if v else None for v in cur["Zc"]],
                "Zc_im": [v.imag if v else None for v in cur["Zc"]],
                "theta_deg": [math.degrees(t) if t is not None else None for t in cur["theta"]],
                "alpha_neper": [g.real if g else None for g in cur["gamma_l"]]}
        _write_curve_csv(csv_path, freq, rows)
        if zc:
            print("[snp] Zc = %.4g %+.4gj ohm" % (zc.real, zc.imag))
        if th_ex is not None:
            print("[snp] theta = %.4g deg" % summary["theta_deg"])
        if "eps_eff" in summary:
            print("[snp] eps_eff = %.6g, alpha = %.6g dB/m" %
                  (summary["eps_eff"], summary["alpha_db_per_m"]))
    else:  # coupled_tl
        z0e, z0o = cur["Z0e"][i_ex], cur["Z0o"][i_ex]
        gate = args.theory_gate if args.theory_gate is not None else THEORY_GATE_DEFAULT
        rel = cur["rel_off"][i_ex]
        summary = {
            "Z0e_ohm": sc.cdict(z0e) if z0e else None,
            "Z0o_ohm": sc.cdict(z0o) if z0o else None,
            "theta_e_deg": math.degrees(cur["theta_e"][i_ex]) if cur["theta_e"][i_ex] is not None else None,
            "theta_o_deg": math.degrees(cur["theta_o"][i_ex]) if cur["theta_o"][i_ex] is not None else None,
            "k": ((z0e - z0o) / (z0e + z0o)).real if (z0e and z0o and (z0e + z0o) != 0) else None,
            "sqrt_Z0eZ0o_ohm": sc.csqrt_pos(z0e * z0o).real if (z0e and z0o) else None,
            "rel_offblock": rel,
            "theory_ok": 1 if (rel is not None and rel <= gate) else 0,
            "theory_gate": gate,
            "f_theta_e90_hz": sc.crossing_at(freq, cur["theta_e"], math.pi / 2),
            "f_theta_o90_hz": sc.crossing_at(freq, cur["theta_o"], math.pi / 2),
        }
        rows = {
            "Z0e_re": [v.real if v else None for v in cur["Z0e"]],
            "Z0e_im": [v.imag if v else None for v in cur["Z0e"]],
            "Z0o_re": [v.real if v else None for v in cur["Z0o"]],
            "Z0o_im": [v.imag if v else None for v in cur["Z0o"]],
            "theta_e_deg": [math.degrees(t) if t is not None else None for t in cur["theta_e"]],
            "theta_o_deg": [math.degrees(t) if t is not None else None for t in cur["theta_o"]],
            "rel_offblock": cur["rel_off"],
        }
        _write_curve_csv(csv_path, freq, rows)
        if z0e and z0o:
            print("[snp] Z0e = %8.3f %+.3fj ohm   Z0o = %8.3f %+.3fj ohm"
                  % (z0e.real, z0e.imag, z0o.real, z0o.imag))
            print("[snp] theta_e = %7.3f deg   theta_o = %7.3f deg"
                  % (summary["theta_e_deg"] or float("nan"), summary["theta_o_deg"] or float("nan")))
            print("[snp] k = %.4f   sqrt(Z0e*Z0o) = %.3f ohm"
                  % (summary["k"] or float("nan"), summary["sqrt_Z0eZ0o_ohm"] or float("nan")))
        print("[snp] rel off-block = %.3e (gate %.3e) -> theory_ok = %d"
              % (rel if rel is not None else float("nan"), gate, summary["theory_ok"]))
        if summary["f_theta_e90_hz"]:
            print("[snp] f(theta_e=90) = %.4g Hz" % summary["f_theta_e90_hz"])
        if summary["f_theta_o90_hz"]:
            print("[snp] f(theta_o=90) = %.4g Hz" % summary["f_theta_o90_hz"])

    result["summary"] = _clean(summary)
    result["results_file"] = str(json_path)
    result["curve_csv"] = str(csv_path)
    sc.write_json_atomic(json_path, result)
    print("[snp] wrote %s" % json_path)
    print("[snp] wrote %s" % csv_path)
    return 1 if unphysical else 0


def _cand_report(ev, chosen):
    rep = {
        "origin": ev["plan"]["origin"],
        "summary": plan_roles_str(ev["plan"]),
        "ok": ev["ok"],
        "reasons": ev["reasons"],
        "rank": ev["rank"] if ev["ok"] else None,
        "chosen": chosen,
    }
    return rep


def _print_summary(summary, items):
    for disp, key, unit in items:
        val = summary.get(key)
        if val is not None:
            print("[snp] %s = %.6g%s%s" % (disp, val, " " if unit else "", unit))


def _print_srf(summary, keys):
    for key in keys:
        v = summary.get(key)
        print("[snp] %s = %s" % (key, "%.6g Hz" % v if v else "none (no +->- crossing)"))


def _write_curve_csv(path, freq, columns):
    names = ["freq_hz"] + list(columns.keys())
    lines = [",".join(names)]
    n = len(freq)
    for i in range(n):
        row = ["%.10e" % freq[i]]
        for key in columns:
            v = columns[key][i]
            row.append("" if v is None else "%.12e" % v)
        lines.append(",".join(row))
    Path(path).write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")


def _clean(obj):
    """Replace non-finite floats with None for JSON stability."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # keep the 0/1/2 exit-code contract intact
        print("[snp] error: unexpected failure: %s" % e, file=sys.stderr)
        sys.exit(2)
