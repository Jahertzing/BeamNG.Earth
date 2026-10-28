"""Colorado's 2023 NAIP (0.3 m, USDA, public domain) from Microsoft's Planetary Computer, for imagery source "co_naip2023".

USGS's NAIP service still serves Colorado's 2019 flight (0.6 m) at ~15 s an image, and USDA's own (2023) does not resolve
from every PC. The Planetary Computer keeps every NAIP flight as COGs (~2 GB per quarter quad: reading windows of them
costs 30-40 x an export's bytes) and renders them through its data API (TiTiler-pgSTAC): a registered STAC search is a
mosaic with XYZ tiles, and a z15 tile at @4x is 1024 px, one export parent of z17 tiles, ~380 KB in ~1.6 s (2026-10-28),
the items picked and joined by the service. A search id expires after 24 h (the number in "pc1.<expiry>.<hash>"), so
the search is registered again (one POST) when its id is near its end, and the id is kept on disk between runs.

Where no 2023 item lies the tile is a 404 (the next source fills in); past the state line inside a tile, black.
"""
import json
import logging
import os
import threading
import time
import urllib.request

import netfetch

log = logging.getLogger("earthd.imagery")

API = "https://planetarycomputer.microsoft.com/api/data/v1/mosaic"
SEARCH = {"collections": ["naip"], "filter-lang": "cql2-json",
          "filter": {"op": "and", "args": [{"op": "=", "args": [{"property": "naip:year"}, "2023"]},
                                           {"op": "=", "args": [{"property": "naip:state"}, "co"]}]},
          "sortby": [{"field": "datetime", "direction": "desc"}]}
# {pz}/{px}/{py}: the export parent; {scale}: its size over 256 px (an export of TILE << 2 = 1024 px: 4)
TILES = (API + "/{searchid}/tiles/WebMercatorQuad/{{pz}}/{{px}}/{{py}}@{{scale}}x.jpg"
         "?assets=image&asset_bidx=image%7C1%2C2%2C3&collection=naip")
RENEW_S = 3600.0                # registered again this long before the id expires
_lock = threading.Lock()
_state = {"searchid": None, "expires": 0.0}


def expiry(searchid):
    """The expiry (Unix s) in a "pc1.<expiry>.<hash>" search id, else 0."""
    try:
        return float(searchid.split(".")[1])
    except (AttributeError, IndexError, ValueError):
        return 0.0


def _register(fetcher):
    if fetcher is not None and getattr(fetcher, "offline_flag", False):
        raise netfetch.FetchFailed("offline", API, netfetch.host_of(API))
    hd = fetcher.headers(netfetch.host_of(API)) if fetcher is not None else {"User-Agent": netfetch.USER_AGENT}
    req = urllib.request.Request(API + "/register", data=json.dumps(SEARCH).encode(), method="POST",
                                 headers={**hd, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            sid = json.loads(r.read().decode("utf-8"))["searchid"]
    except Exception as e:  # noqa: BLE001 (any failure: the source is passed over, as a host that is down)
        raise netfetch.FetchFailed(f"Planetary Computer search registration: {type(e).__name__}: {e}", API,
                                   netfetch.host_of(API)) from None
    log.info("imagery: Planetary Computer NAIP 2023 search %s (until %s)", sid,
             time.strftime("%Y-%m-%d %H:%M", time.gmtime(expiry(sid))))
    return sid


def template(fetcher, cache_dir=None, now=time.time):
    """The URL template of the mosaic's tiles ({pz}, {px}, {py}, {scale} left to fill): the search id from memory,
    else from <cache_dir>/pcnaip.json, else registered now (raises netfetch.FetchFailed when that fails)."""
    with _lock:
        sid = _state["searchid"]
        if sid is None or _state["expires"] - now() < RENEW_S:
            path = os.path.join(cache_dir, "pcnaip.json") if cache_dir else None
            sid = None
            if path:
                try:
                    with open(path, encoding="utf-8") as f:
                        sid = json.load(f).get("searchid")
                except (OSError, ValueError, AttributeError):
                    sid = None
            if sid is None or expiry(sid) - now() < RENEW_S:
                sid = _register(fetcher)
                if path:
                    os.makedirs(cache_dir, exist_ok=True)
                    tmp = f"{path}.{os.getpid()}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump({"searchid": sid}, f)
                    os.replace(tmp, path)
            _state.update(searchid=sid, expires=expiry(sid))
        return TILES.format(searchid=sid)


def reset():
    """Forget the search id (tests)."""
    with _lock:
        _state.update(searchid=None, expires=0.0)
