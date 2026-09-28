"""Repro for the deflated-SVD LinearOperator shape bug in likes_spectral.

Two checks, both run against the installed scipy:

1. `current_matvec_shape()` — the operator as written in
   data_engineering.features.likes_spectral._truncated_svd, fed the COLUMN
   vector scipy hands it (`col.reshape(-1, 1)` in
   scipy.sparse.linalg._interface.LinearOperator._matmat). The rank-1
   correction broadcasts to a full matrix instead of a column, so the result
   has shape (n_users, n_users) where (n_users, 1) was required.
2. `candidate_fix()` — the same operator written matmat-aware (reshape to a
   2-D block, `np.outer` for the correction, ravel back for 1-D input).
   Singular values are compared against the dense reference.

Run:  uv run python 2026-09-02-repo-learnings/scripts/repro_spectral_matvec.py
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, svds

# 6 users x 5 posts, the size band the failing unit tests use. Deliberately
# TWO components (the bridge user ub likes only posts inside the first block),
# so sigma=1 has multiplicity 2 and survives the deflation of one trivial
# triplet — irrelevant here, since the point is only that the operator and the
# dense reference agree.
A = sp.csr_matrix(
    np.array(
        [
            [1.0, 1, 1, 0, 0],
            [1, 1, 1, 0, 0],
            [1, 1, 1, 0, 0],
            [0, 0, 0, 1, 1],
            [0, 0, 0, 1, 1],
            [0, 1, 1, 0, 0],
        ]
    )
)


def normalized(A):
    d_user = np.asarray(A.sum(axis=1)).ravel()
    d_post = np.asarray(A.sum(axis=0)).ravel()
    A_hat = (sp.diags(1 / np.sqrt(d_user)) @ A @ sp.diags(1 / np.sqrt(d_post))).tocsr()
    total = float(d_user.sum())
    return A_hat, np.sqrt(d_user / total), np.sqrt(d_post / total)


def current_matvec_shape() -> tuple:
    """Shape the CURRENT matvec returns for a (n_posts, 1) input."""
    A_hat, u0, v0 = normalized(A)
    x = np.ones((A_hat.shape[1], 1))
    return (A_hat @ x - u0 * (v0 @ x)).shape


def candidate_fix(k: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """(svds singular values, dense reference singular values)."""
    A_hat, u0, v0 = normalized(A)

    def mv(x):
        x = np.asarray(x)
        flat = x.ndim == 1
        X = x.reshape(x.shape[0], -1)
        Y = A_hat @ X - np.outer(u0, v0 @ X)
        return Y.ravel() if flat else Y

    def rmv(x):
        x = np.asarray(x)
        flat = x.ndim == 1
        X = x.reshape(x.shape[0], -1)
        Y = A_hat.T @ X - np.outer(v0, u0 @ X)
        return Y.ravel() if flat else Y

    op = LinearOperator(A_hat.shape, matvec=mv, rmatvec=rmv, matmat=mv, rmatmat=rmv, dtype=float)
    _, s, _ = svds(op, k=k)
    dense = np.linalg.svd(A_hat.toarray() - np.outer(u0, v0), compute_uv=False)
    return np.sort(s)[::-1], dense[:k]


if __name__ == "__main__":
    print(f"current matvec on a (5, 1) column -> {current_matvec_shape()}  (expected (6, 1))")
    got, want = candidate_fix()
    print(f"candidate fix svds sigma {got}  vs dense {want}")
