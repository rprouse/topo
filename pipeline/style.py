"""Starting symbology and labels, following docs/symbol-spec.md.

Used once, by `make project-init`, to generate qgis/fernie.qgs. After that
the project is the source of truth: restyle in QGIS desktop and commit the
project. Keep docs/symbol-spec.md in step with what you change.

All sizes are millimetres on paper unless noted; text sizes are points.
"""

from __future__ import annotations

FONT = "Noto Sans"
MIN_LINE_MM = 0.1
MIN_TEXT_PT = 6.5

# Colours (chosen to convert cleanly to CMYK).
C = {
    "black": "#1a1a1a",
    "contour": "#a0623a",
    "water": "#2b7bc4",
    "water_fill": "#a9d1ef",
    "water_text": "#1f5f99",
    "forest": "#dcebc8",
    "scrub": "#e7efcf",
    "grass": "#f1f4dc",
    "heath": "#ecefd6",
    "rock": "#e6e1da",
    "scree": "#ebe6de",
    "glacier": "#eaf4fb",
    "farmland": "#f3f0dc",
    "urban": "#ebe4dc",
    "industrial": "#e2dcd8",
    "quarry": "#e0d8cf",
    "cemetery": "#dbe6cf",
    "park": "#d3e8bd",
    "highway": "#d7372a",
    "major": "#f2a33a",
    "paved": "#fff3a6",
    "trail_red": "#d7191c",
    "building": "#4d4d4d",
    "power": "#6e6e6e",
    "protected": "#3d8b37",
    "place_text": "#1a1a1a",
    "label_halo": "#ffffff",
}

HILLSHADE_OPACITY = 0.45

LANDCOVER = [  # class, colour
    ("forest", "forest"), ("scrub", "scrub"), ("heath", "heath"), ("grass", "grass"),
    ("rock", "rock"), ("scree", "scree"), ("glacier", "glacier"), ("farmland", "farmland"),
    ("urban", "urban"), ("industrial", "industrial"), ("quarry", "quarry"), ("cemetery", "cemetery"),
    ("golf", "park"), ("park", "park"),
]

# Roads, drawn bottom to top. (class, legend label, casing (colour, width) or None, line spec)
ROADS = [
    ("track_4wd", "4x4 track", None, {"color": C["black"], "width": 0.22, "dash": (1.0, 0.8)}),
    ("track", "Track", None, {"color": C["black"], "width": 0.3, "dash": (2.0, 0.7)}),
    ("gravel", "Gravel road", None, {"color": C["black"], "width": 0.35}),
    ("fsr", "Forest service road", (C["black"], 0.65), {"color": "#ffffff", "width": 0.35}),
    ("paved", "Paved road", (C["black"], 0.75), {"color": C["paved"], "width": 0.5}),
    ("major", "Major road", (C["black"], 0.95), {"color": C["major"], "width": 0.7}),
    ("highway", "Highway", (C["black"], 1.15), {"color": C["highway"], "width": 0.85}),
]

# Trails: one symbol for every trail, plus paved paths. (filter, legend label, colour, width, dash)
TRAILS = [
    ('"class" = \'cycleway\'', "Paved path", C["trail_red"], 0.3, None),
    ("ELSE", "Trail", C["trail_red"], 0.35, (0.35, 0.5)),
]

POIS = [  # class, legend label, marker properties
    ("peak", "Peak", {"name": "triangle", "color": C["black"], "size": "1.8", "outline_style": "no"}),
    ("saddle", "Pass / saddle", {"name": "half_arc", "color": C["black"], "size": "1.6", "outline_width": "0.25"}),
    ("viewpoint", "Viewpoint", {"name": "star", "color": C["black"], "size": "2.0", "outline_style": "no"}),
    ("hut", "Hut", {"name": "square", "color": C["trail_red"], "size": "1.8", "outline_color": C["black"], "outline_width": "0.2"}),
    ("shelter", "Shelter", {"name": "triangle", "color": "#ffffff", "size": "1.8", "outline_color": C["trail_red"], "outline_width": "0.3"}),
    ("trailhead", "Trailhead", {"name": "circle", "color": C["trail_red"], "size": "1.6", "outline_color": "#ffffff", "outline_width": "0.2"}),
    ("parking", "Parking", {"name": "square", "color": "#1f5f99", "size": "1.8", "outline_style": "no"}),
    ("campsite", "Campsite", {"name": "triangle", "color": C["protected"], "size": "1.8", "outline_style": "no"}),
    ("waterfall", "Waterfall", {"name": "line", "color": C["water"], "size": "1.8", "outline_width": "0.4"}),
]

PLACE_SIZES = {"city": 14, "town": 11, "village": 9, "suburb": 8, "neighbourhood": 7.5,
               "hamlet": 7.5, "locality": 7, "isolated_dwelling": 6.5}

# Label priorities (0-10, higher wins).
PRIORITY = {"places": 10, "peaks": 9, "water_areas": 8, "names": 8, "water_lines": 7, "trails": 6,
            "roads": 5, "spot_heights": 4, "pois": 4, "contours": 1}

# Only draw features that a correction hasn't hidden.
VISIBLE = 'coalesce("hidden", 0) = 0'
