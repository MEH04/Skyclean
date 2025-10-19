import os, glob, re
import numpy as np
import healpy as hp

from .utils import _norm
# mixing_matrix_constraint.py
# Minimal, direct fixes:
# - NO normalization anywhere (F and reference_vectors are raw).
# - Column order of F is FLEXIBLE and follows the user-provided components_order.
# - f is built ROBUSTLY to match the actual column order of F (order-agnostic).
# - helper find_f_from_names() provided for direct name->column mapping.


def find_f_from_names(component_names, selected):
    """
    Build constraint vector f purely from name->column mapping,
    independent of where components sit in F.

    Args:
        component_names (list[str]): Column names of F in order, e.g. ["tsz","cmb","sync"].
        selected (str | list[str]): Target component name(s) to set to 1.

    Returns:
        f (np.ndarray): 0/1 vector aligned to component_names (length Nc).
        idxs (list[int]): Column indices that were set to 1.
    """
    if isinstance(selected, (str, bytes)):
        selected = [selected]
    names_lower = [str(n).lower() for n in component_names]
    f = np.zeros(len(component_names), dtype=float)
    idxs = []
    for name in selected:
        key = str(name).lower()
        try:
            idx = names_lower.index(key)
        except ValueError:
            raise ValueError(f"{name} not in component_names={component_names}")
        if idx not in idxs:  # avoid duplicates
            f[idx] = 1.0
            idxs.append(idx)
    return f, idxs


class SpectralVector:
    """
    Build F (N_freq x N_comp) and reference_vectors (raw, no normalization).

    Flexible column order:
      - Pass components_order = ["cmb","tsz","sync"] (or any order/subset).
      - Unknown names (e.g. 'noise') are ignored by the theory builder;
        empirical builder also requires presence in file_templates.
    Supported theory keys: 'cmb', 'tsz', 'sync'
    """

    @staticmethod
    def build_F_theory(
        beta_s: float = -3.1,
        nu0: float = 30e9,
        frequencies: list[str] | None = None,
        components_order: list[str] | None = None,  # desired column order
    ):
        """
        Returns:
            F (np.ndarray): shape (Nf, Nc) raw spectral responses in K_CMB.
            F_cols (list[str]): column names, in the order used to build F.
            reference_vectors (dict[str, np.ndarray]): raw vectors keyed by name.
            frequencies_out (list[str]): the frequency tags actually used.
        """
        # channels
        if frequencies is None:
            frequencies = ['030','044','070','100','143','217','353','545','857']
        nu = np.array([float(f) for f in frequencies]) * 1e9

        # thermodynamic K_CMB conversions
        h = 6.62607015e-34
        k = 1.380649e-23
        T = 2.7255
        x = h * nu / (k * T)
        g_nu = (x**2 * np.exp(x)) / (np.exp(x) - 1.0)**2

        # RAW spectral response vectors (no normalization)
        vecs = {
            "cmb":  np.ones_like(nu),
            "tsz":  x * ((np.exp(x) + 1.0) / (np.exp(x) - 1.0)) - 4.0,
            "sync": (nu / float(nu0)) ** float(beta_s) / g_nu,
        }

        # Decide column order: keep ONLY supported names, IN THE GIVEN ORDER
        if components_order is None:
            F_cols = ["cmb", "tsz", "sync"]
        else:
            wanted = [str(c).lower() for c in components_order]
            F_cols = [c for c in wanted if c in vecs]

        reference_vectors = {c: vecs[c] for c in F_cols}  # RAW
        F = np.column_stack([reference_vectors[c] for c in F_cols]) if F_cols else np.zeros((len(nu), 0))
        print ('F_theory:', F)
        return F, F_cols, reference_vectors, list(frequencies)

    @staticmethod
    def build_F_empirical(
        base_dir: str,
        file_templates: dict[str, str],
        frequencies: list[str],
        realization: int = 0,
        mask_path: str = "",
        components_order: list[str] | None = None,  # desired column order
    ):
        """
        Empirical F: per-component, per-frequency masked means (RAW, no normalization).
        Requires file_templates entries for the chosen components (e.g. 'cmb','tsz','sync').

        Returns:
            F (np.ndarray): (Nf, Nc)
            F_cols (list[str])
            reference_vectors (dict[str, np.ndarray])
            frequencies_out (list[str])
        """
        def _mean(path, mask):
            M = hp.read_map(path, verbose=False)
            if mask is not None:
                m = hp.ud_grade(mask, nside_out=hp.get_nside(M), power=0) > 0
                vals = M[np.isfinite(M) & m]
            else:
                vals = M[np.isfinite(M)]
            return vals.mean() if vals.size else 0.0

        mask = hp.read_map(mask_path, verbose=False) if mask_path else None

        # Decide order (keep only components present in file_templates), input order preserved
        default = ["cmb", "tsz", "sync"]
        wanted = [str(c).lower() for c in (components_order or default)]
        F_cols = [c for c in wanted if c in file_templates and file_templates[c] is not None]
        if not F_cols:
            raise ValueError("No valid components in file_templates for empirical F.")

        reference_vectors = {}
        for comp in F_cols:
            tmpl = file_templates[comp]
            v = np.zeros(len(frequencies), dtype=float)
            for i, f in enumerate(frequencies):
                path = os.path.join(base_dir, tmpl.format(frequency=f, realisation=realization))
                if os.path.exists(path):
                    v[i] = _mean(path, mask)
            reference_vectors[comp] = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)  # RAW

        F = np.column_stack([reference_vectors[c] for c in F_cols]) if F_cols else np.zeros((len(frequencies), 0))
        print ('F_empirical:', F)
        return F, F_cols, reference_vectors, list(frequencies)

    @staticmethod
    def get_F(source: str = "theory", **kwargs):
        """
        source: "theory" or "empirical"
        Pass components_order=[...] to control/align the column order of F.
        """
        if source == "theory":
            return SpectralVector.build_F_theory(**kwargs)
        elif source == "empirical":
            return SpectralVector.build_F_empirical(**kwargs)
        else:
            raise ValueError("source must be 'theory' or 'empirical'.")


class ILCConstraints:
    """
    Minimal, robust helpers to build f aligned to F's actual column order.
    """

    @staticmethod
    def find_f_from_extract_comp(F, extract_comp_or_comps, reference_vectors, F_cols=None):
        """
        Build f aligned to the *columns of F*. Minimal change vs your original:
        - If F_cols is provided, align to it (recommended).
        - Else fall back to the key order of reference_vectors (old behavior).

        Returns:
            f (np.ndarray): length = F.shape[1], with 1s for selected targets.
        """
        # normalize to list
        if isinstance(extract_comp_or_comps, (str, bytes)):
            names = [extract_comp_or_comps]
        else:
            names = list(extract_comp_or_comps)

        # choose column ordering to align with
        if F_cols is None:
            F_cols = list(reference_vectors.keys())

        # delegate to the robust name->column mapper
        f, _ = find_f_from_names(F_cols, names)
        print('f:', f)
        return f
