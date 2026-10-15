"""Frontend run matrix (WP10): viewports, locales, colour schemes, reduced motion.

`POST /api/runs` (body) or the suite settings chain name any of:

    viewports:      ["mobile", "desktop", {"name": "kiosk", "width": 1080, "height": 1920}]
    locales:        ["en-US", {"locale": "ar-EG", "query": {"lang": "ar"},
                               "cookie": {"name": "locale", "value": "ar"},
                               "url_template": "{origin}/ar{path}"}]
    color_schemes:  ["light", "dark"]
    reduced_motion: true

and the run fans out over their cartesian product exactly like the browser x
device matrix does: one TestRun per combination, each carrying its own
`emulation` dict, all sharing one `sweep_group`. The worker turns `emulation`
into Playwright context options (viewport, locale + Accept-Language,
colorScheme, reducedMotion) and the locale's URL/query/cookie rules.

Everything here is pure so the fan-out arithmetic and the validation can be
unit-tested without a database.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

VIEWPORT_PRESETS: Dict[str, Dict[str, int]] = {
    "mobile": {"width": 375, "height": 812},
    "tablet": {"width": 768, "height": 1024},
    "desktop": {"width": 1366, "height": 768},
    "wide": {"width": 1920, "height": 1080},
}
COLOR_SCHEMES = ("light", "dark", "no-preference")

MAX_VIEWPORTS = 8
MAX_LOCALES = 10
# Upper bound on runs ONE request may create (browsers x devices x variants x
# identities). The device matrix alone was bounded by the device list; three
# more multiplying axes are not, and a typo should not enqueue 2,000 runs.
MAX_FANOUT_RUNS = 120

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
# BCP 47-ish: language (2-3 letters) + optional subtags. Enough to refuse
# garbage, not a full registry check.
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")
_QUERY_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]{1,64}$")
_COOKIE_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
_TEMPLATE_FIELDS = {"origin", "path", "query", "url", "locale", "lang"}
_TEMPLATE_FIELD_RE = re.compile(r"\{([a-z]+)\}")
# Locales whose script is written right to left: the RTL check step's default
# expectation, and a hint shown in the sweep view.
RTL_LANGUAGES = {"ar", "he", "fa", "ur", "ps", "sd", "yi", "dv", "ckb", "ug"}


class MatrixError(ValueError):
    """An invalid frontend-matrix option; the API turns it into a 400."""


def _viewport(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        key = raw.strip().lower()
        if key not in VIEWPORT_PRESETS:
            raise MatrixError(
                f"unknown viewport preset {raw!r} (presets: {', '.join(VIEWPORT_PRESETS)})")
        return {"name": key, **VIEWPORT_PRESETS[key]}
    if not isinstance(raw, dict):
        raise MatrixError("a viewport is a preset name or {name, width, height}")
    name = str(raw.get("name") or "").strip()
    if not _NAME_RE.match(name):
        raise MatrixError("viewport name must be 1-40 letters, digits, space, _ . -")
    preset = VIEWPORT_PRESETS.get(name.lower(), {})
    width, height = raw.get("width", preset.get("width")), raw.get("height", preset.get("height"))
    for label, value, lo, hi in (("width", width, 200, 3840), ("height", height, 200, 4320)):
        if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
            raise MatrixError(f"viewport {name!r}: {label} must be an integer {lo}-{hi}")
    return {"name": name, "width": width, "height": height}


def normalise_viewports(raw: Any) -> List[Dict[str, Any]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise MatrixError("viewports must be a list")
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        vp = _viewport(item)
        if vp["name"].lower() in seen:
            continue
        seen.add(vp["name"].lower())
        out.append(vp)
    if len(out) > MAX_VIEWPORTS:
        raise MatrixError(f"at most {MAX_VIEWPORTS} viewports per run")
    return out


def _template(raw: Any, locale: str) -> Optional[str]:
    if raw in (None, ""):
        return None
    if not isinstance(raw, str) or len(raw) > 300:
        raise MatrixError(f"locale {locale}: url_template must be a string of at most 300 chars")
    fields = set(_TEMPLATE_FIELD_RE.findall(raw))
    unknown = fields - _TEMPLATE_FIELDS
    if unknown:
        raise MatrixError(f"locale {locale}: url_template has unknown field(s) "
                          f"{sorted(unknown)} (allowed: {sorted(_TEMPLATE_FIELDS)})")
    if not fields & {"origin", "url"}:
        # Without the origin the template could point the journey anywhere; the
        # locale rule may only rewrite the path/query of the URL it was given.
        raise MatrixError(f"locale {locale}: url_template must start from {{origin}} or {{url}}")
    if not (raw.startswith("{origin}") or raw.startswith("{url}")):
        raise MatrixError(f"locale {locale}: url_template must start with {{origin}} or {{url}}")
    return raw


def _locale(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        raw = {"locale": raw}
    if not isinstance(raw, dict):
        raise MatrixError("a locale is a BCP 47 tag or {locale, query?, cookie?, url_template?}")
    tag = str(raw.get("locale") or "").strip()
    if not _LOCALE_RE.match(tag):
        raise MatrixError(f"invalid locale {tag!r} (expected a BCP 47 tag such as en-US)")
    out: Dict[str, Any] = {"locale": tag}
    query = raw.get("query")
    if query not in (None, {}):
        if not isinstance(query, dict) or len(query) > 10:
            raise MatrixError(f"locale {tag}: query must be an object of at most 10 parameters")
        clean = {}
        for k, v in query.items():
            if not _QUERY_KEY_RE.match(str(k)) or not isinstance(v, (str, int)) or len(str(v)) > 100:
                raise MatrixError(f"locale {tag}: invalid query parameter {k!r}")
            clean[str(k)] = str(v)
        out["query"] = clean
    cookie = raw.get("cookie")
    if cookie not in (None, {}):
        if (not isinstance(cookie, dict) or not _COOKIE_NAME_RE.match(str(cookie.get("name") or ""))
                or not isinstance(cookie.get("value"), (str, int)) or len(str(cookie.get("value"))) > 200
                or any(c in str(cookie.get("value")) for c in ";\r\n")):
            raise MatrixError(f"locale {tag}: cookie must be {{name, value}} with a plain token name")
        out["cookie"] = {"name": str(cookie["name"]), "value": str(cookie["value"])}
        if cookie.get("domain"):
            domain = str(cookie["domain"]).strip().lower()
            if not re.match(r"^\.?[a-z0-9.-]{1,253}$", domain):
                raise MatrixError(f"locale {tag}: invalid cookie domain")
            out["cookie"]["domain"] = domain
    template = _template(raw.get("url_template"), tag)
    if template:
        out["url_template"] = template
    return out


def normalise_locales(raw: Any) -> List[Dict[str, Any]]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise MatrixError("locales must be a list")
    out, seen = [], set()
    for item in raw:
        loc = _locale(item)
        if loc["locale"].lower() in seen:
            continue
        seen.add(loc["locale"].lower())
        out.append(loc)
    if len(out) > MAX_LOCALES:
        raise MatrixError(f"at most {MAX_LOCALES} locales per run")
    return out


def normalise_color_schemes(raw: Any) -> List[str]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list):
        raise MatrixError("color_schemes must be a list")
    out: List[str] = []
    for item in raw:
        value = str(item or "").strip().lower()
        if value not in COLOR_SCHEMES:
            raise MatrixError(f"unknown color scheme {item!r} (allowed: {', '.join(COLOR_SCHEMES)})")
        if value not in out:
            out.append(value)
    return out


def is_rtl_locale(tag: Optional[str]) -> bool:
    return bool(tag) and tag.split("-")[0].lower() in RTL_LANGUAGES


def variant_label(variant: Dict[str, Any]) -> str:
    parts = []
    if variant.get("viewport"):
        vp = variant["viewport"]
        parts.append(f"{vp['name']} {vp['width']}x{vp['height']}")
    if variant.get("locale"):
        parts.append(variant["locale"]["locale"])
    if variant.get("color_scheme"):
        parts.append(variant["color_scheme"])
    if variant.get("reduced_motion"):
        parts.append("reduced motion")
    return " · ".join(parts)


def baseline_key(variant: Dict[str, Any]) -> Optional[str]:
    """The `device` a visual baseline is stored under for this variant.

    A mobile screenshot compared against the desktop baseline fails for the
    wrong reason, so each variant gets its own baseline slot. `vp:` keys are
    resolved exact-match only (app/api/visual_baselines.py)."""
    parts = []
    if variant.get("viewport"):
        parts.append(variant["viewport"]["name"])
    if variant.get("locale"):
        parts.append(variant["locale"]["locale"])
    if variant.get("color_scheme"):
        parts.append(variant["color_scheme"])
    if variant.get("reduced_motion"):
        parts.append("rm")
    return ("vp:" + "|".join(parts)) if parts else None


def resolve_options(body: Any, effective_settings: Dict[str, Any]) -> Dict[str, Any]:
    """Explicit request options win over the suite chain's, per axis."""
    def pick(name):
        value = getattr(body, name, None) if body is not None else None
        return value if value not in (None, []) else effective_settings.get(name)

    reduced = pick("reduced_motion")
    return {
        "viewports": normalise_viewports(pick("viewports")),
        "locales": normalise_locales(pick("locales")),
        "color_schemes": normalise_color_schemes(pick("color_schemes")),
        "reduced_motion": bool(reduced) if reduced is not None else False,
        # Screenshot after every step so the sweep view can line the
        # variants up step by step (only meaningful with >1 variant).
        "step_screenshots": bool(pick("sweep_step_screenshots")) if pick("sweep_step_screenshots") is not None else None,
    }


def build_variants(options: Dict[str, Any]) -> List[Optional[Dict[str, Any]]]:
    """Cartesian product of the axes; [None] when no axis is set (ordinary run)."""
    viewports = options.get("viewports") or [None]
    locales = options.get("locales") or [None]
    schemes = options.get("color_schemes") or [None]
    reduced = bool(options.get("reduced_motion"))
    if viewports == [None] and locales == [None] and schemes == [None] and not reduced:
        return [None]
    variants: List[Optional[Dict[str, Any]]] = []
    for vp in viewports:
        for loc in locales:
            for scheme in schemes:
                v: Dict[str, Any] = {}
                if vp:
                    v["viewport"] = vp
                if loc:
                    v["locale"] = loc
                if scheme:
                    v["color_scheme"] = scheme
                if reduced:
                    v["reduced_motion"] = True
                v["label"] = variant_label(v)
                v["baseline_key"] = baseline_key(v)
                variants.append(v)
    multi = len(variants) > 1
    step_shots = options.get("step_screenshots")
    for v in variants:
        # Default on for a real sweep (several widths side by side is the
        # point), off for a single emulated variant.
        v["step_screenshots"] = multi if step_shots is None else bool(step_shots)
    return variants


def check_fanout(browsers: int, devices: int, variants: int, identities: int) -> int:
    total = max(1, browsers) * max(1, devices) * max(1, variants) * max(1, identities)
    if total > MAX_FANOUT_RUNS:
        raise MatrixError(
            f"this request would create {total} runs per case (browsers {browsers} x devices "
            f"{devices} x variants {variants} x identities {identities}); the limit is "
            f"{MAX_FANOUT_RUNS}. Narrow the matrix.")
    return total
