#!/usr/bin/env python3
"""Draw a living pond for a GitHub profile README.

The pond fills with creatures for the contributions in the last 30 days, and it
never stops growing. Every creature is bought in order, and each tier brings a
new kind of animal: koi, dragonflies, frogs on lily pads, bigger koi, turtles,
ducks, glowing jellyfish, giant koi, sharks, manta rays and whales. Past that
the pond is legendary: sea serpents, krakens and golden dragons keep arriving,
each one a little dearer than the last, so there is always a next creature.
As old contributions fall out of the window the newest creatures leave again.
Each creature keeps the same colors and swimming path from day to day.

Usage:
    GITHUB_TOKEN=... python3 scripts/pond.py --login octocat --output pond/pond.svg
    python3 scripts/pond.py --login octocat --contributions 250 --output preview.svg

Only the Python standard library is used, so a workflow needs no install step.
"""

import argparse
import json
import math
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Contributions are counted over this many days, today included.
WINDOW_DAYS = 30
# Each tier is (creature, how many it holds, contributions per creature, size).
# A tier fills before the next one starts. The very first creature is free, so
# the pond is never empty.
TIERS = [
    ("koi", 10, 1, 0.65),
    ("dragonfly", 3, 3, 1.0),
    ("frog", 4, 5, 1.0),
    ("koi", 4, 8, 1.0),
    ("turtle", 3, 12, 1.0),
    ("duck", 3, 15, 1.0),
    ("jellyfish", 4, 20, 1.0),
    ("koi", 3, 25, 1.45),
    ("shark", 3, 30, 1.0),
    ("manta", 2, 40, 1.0),
    ("whale", 2, 60, 1.0),
]
# After the tiers the pond turns legendary and never fills: legend number k
# costs LEGEND_BASE + LEGEND_STEP * k contributions, cycling through LEGENDS.
LEGENDS = ["sea_serpent", "kraken", "golden_dragon"]
LEGEND_BASE = 100
LEGEND_STEP = 25

NAMES = {
    "koi": "koi",
    "dragonfly": "dragonfly",
    "frog": "frog",
    "turtle": "turtle",
    "duck": "duck",
    "jellyfish": "jellyfish",
    "shark": "shark",
    "manta": "manta ray",
    "whale": "whale",
    "sea_serpent": "sea serpent",
    "kraken": "kraken",
    "golden_dragon": "golden dragon",
}

WIDTH = 800
HEIGHT = 280
EDGE = 18  # how close a swimming path may come to the pond's edge

# One fish in its own coordinates: nose at +x, tail at -x, about 41 units long.
BODY = (
    "M16 0C16-4 11.5-6.6 5-6.6C-3-6.6-9.5-3.2-14-1.3L-14 1.3"
    "C-9.5 3.2-3 6.6 5 6.6C11.5 6.6 16 4 16 0Z"
)
TAIL = "M-13 0C-17-2-20.5-6.5-25.5-8C-23.5-3.5-22.3-1-22.3 0C-22.3 1-23.5 3.5-25.5 8C-20.5 6.5-17 2-13 0Z"
TAIL_JOINT = (-13, 0)

WHITE = "#f8f4ec"
RED = "#e0452c"
ORANGE = "#ec7a32"
BLACK = "#1c1c24"
GOLD = "#f2bd3c"
PLATINUM = "#e6ebef"
ASAGI_BLUE = "#6a8aa4"
SHUSUI_BLUE = "#5a83a6"
CHAGOI_BROWN = "#9c6b3e"
KIGOI_YELLOW = "#f1d54a"

# (name, weight) for every fish after the first, which is always an Asagi.
VARIETIES = [
    ("kohaku", 6),
    ("sanke", 4),
    ("showa", 3),
    ("tancho", 2),
    ("ogon", 2),
    ("platinum", 2),
    ("chagoi", 2),
    ("kigoi", 2),
    ("shusui", 2),
    ("asagi", 2),
    ("shiro_utsuri", 2),
]


# ---------------------------------------------------------------------------
# Counting


def tier_stream():
    """Every creature the pond can ever hold, in order, as (creature, cost, size)."""
    first = True
    for creature, count, cost, size in TIERS:
        for _ in range(count):
            yield creature, 0 if first else cost, size
            first = False
    k = 0
    while True:
        size = min(1.6, 1.0 + 0.08 * (k // len(LEGENDS)))
        yield LEGENDS[k % len(LEGENDS)], LEGEND_BASE + LEGEND_STEP * k, size
        k += 1


def roster(contributions):
    """The creatures `contributions` buy, plus the next creature and what it still costs.

    Each creature is (kind, number among its kind, size). The first one is free.
    """
    creatures, counters, spent = [], {}, 0
    for creature, cost, size in tier_stream():
        if spent + cost > contributions:
            return creatures, creature, spent + cost - contributions
        spent += cost
        number = counters.get(creature, 0)
        counters[creature] = number + 1
        creatures.append((creature, number, size))


def graphql(query, variables, token):
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "koi-pond",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("errors"):
        raise RuntimeError(f"GitHub answered with errors: {body['errors']}")
    return body["data"]


def recent_contributions(login, token, days=WINDOW_DAYS):
    """Contributions on the account's public calendar in the last `days` days, today included."""
    now = datetime.now(timezone.utc)
    first_day = (now - timedelta(days=days - 1)).date()
    data = graphql(
        "query($login: String!, $from: DateTime!, $to: DateTime!) { user(login: $login) "
        "{ contributionsCollection(from: $from, to: $to) "
        "{ contributionCalendar { weeks { contributionDays { date contributionCount } } } } } }",
        {
            "login": login,
            "from": f"{first_day.isoformat()}T00:00:00Z",
            "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        token,
    )
    if data["user"] is None:
        raise RuntimeError(f"no GitHub account named {login!r}")
    weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    # GitHub answers in whole weeks, so drop any days before the window.
    return sum(
        day["contributionCount"]
        for week in weeks
        for day in week["contributionDays"]
        if day["date"] >= first_day.isoformat()
    )


# ---------------------------------------------------------------------------
# Drawing helpers


def num(value):
    """Short, stable number formatting for SVG attributes."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def closed_curve(points):
    """A smooth closed path through `points` (Catmull-Rom turned into cubic Beziers)."""
    count = len(points)
    segments = []
    for index in range(count):
        p0 = points[index - 1]
        p1 = points[index]
        p2 = points[(index + 1) % count]
        p3 = points[(index + 2) % count]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segments.append((p1, c1, c2, p2))
    return segments


def curve_length(segments):
    length = 0.0
    for p0, c1, c2, p3 in segments:
        previous = p0
        for step in range(1, 21):
            t = step / 20
            u = 1 - t
            point = (
                u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * p3[0],
                u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * p3[1],
            )
            length += math.dist(previous, point)
            previous = point
    return length


def curve_path(segments):
    start = segments[0][0]
    parts = [f"M{num(start[0])} {num(start[1])}"]
    for _, c1, c2, end in segments:
        parts.append(
            f"C{num(c1[0])} {num(c1[1])} {num(c2[0])} {num(c2[1])} {num(end[0])} {num(end[1])}"
        )
    return "".join(parts) + "Z"


def swim_path(rng, edge=EDGE):
    """A loop somewhere in the pond, as a path string and its length in pixels."""
    center_x = rng.uniform(140, WIDTH - 140)
    center_y = rng.uniform(80, HEIGHT - 80)
    radius_x = rng.uniform(80, 260)
    radius_y = rng.uniform(35, 105)
    point_count = rng.randint(5, 7)
    turn = rng.uniform(0, math.tau)
    direction = rng.choice((1, -1))
    points = []
    for index in range(point_count):
        angle = turn + direction * math.tau * (index + rng.uniform(-0.2, 0.2)) / point_count
        reach = rng.uniform(0.6, 1.0)
        x = center_x + math.cos(angle) * radius_x * reach
        y = center_y + math.sin(angle) * radius_y * reach
        points.append((min(max(x, edge), WIDTH - edge), min(max(y, edge), HEIGHT - edge)))
    segments = closed_curve(points)
    return curve_path(segments), curve_length(segments)


def ellipse(cx, cy, rx, ry, angle, fill):
    return (
        f'<ellipse cx="{num(cx)}" cy="{num(cy)}" rx="{num(rx)}" ry="{num(ry)}" '
        f'transform="rotate({num(angle)} {num(cx)} {num(cy)})" fill="{fill}"/>'
    )


def patches(rng, count, color, size=1.0):
    shapes = []
    for _ in range(count):
        shapes.append(
            ellipse(
                rng.uniform(-11, 13),
                rng.uniform(-4.5, 4.5),
                rng.uniform(3, 6.5) * size,
                rng.uniform(2.2, 4.5) * size,
                rng.uniform(-40, 40),
                color,
            )
        )
    return shapes


def dots(rng, count, color):
    return [
        f'<circle cx="{num(rng.uniform(-11, 12))}" cy="{num(rng.uniform(-4.5, 4.5))}" '
        f'r="{num(rng.uniform(0.7, 1.7))}" fill="{color}"/>'
        for _ in range(count)
    ]


def flanks(rng, color):
    """Color along both edges of the body, as on Asagi and Shusui."""
    shapes = []
    for side in (-1, 1):
        for _ in range(rng.randint(2, 3)):
            shapes.append(
                ellipse(rng.uniform(-10, 9), side * 6.6, rng.uniform(5, 9), rng.uniform(1.8, 2.8), 0, color)
            )
    return shapes


def coat(variety, rng):
    """Base color, fin color, and the markings drawn inside the body for a variety."""
    if variety == "kohaku":
        return WHITE, WHITE, patches(rng, rng.randint(2, 4), RED)
    if variety == "sanke":
        return WHITE, WHITE, patches(rng, rng.randint(2, 3), RED) + dots(rng, rng.randint(3, 6), BLACK)
    if variety == "showa":
        return BLACK, "#3a3a44", patches(rng, rng.randint(2, 3), RED) + patches(rng, rng.randint(1, 3), WHITE, 0.8)
    if variety == "tancho":
        return WHITE, WHITE, [f'<circle cx="10.5" cy="0" r="{num(rng.uniform(2.6, 3.4))}" fill="{RED}"/>']
    if variety == "ogon":
        return GOLD, "#f7d77a", []
    if variety == "platinum":
        return PLATINUM, "#f4f6f8", []
    if variety == "chagoi":
        return CHAGOI_BROWN, "#b88a5c", []
    if variety == "kigoi":
        return KIGOI_YELLOW, "#f5e38a", []
    if variety == "shusui":
        scales = [f'<circle cx="{x}" cy="0" r="0.9" fill="#26394d"/>' for x in range(12, -13, -3)]
        return SHUSUI_BLUE, "#e9a070", flanks(rng, ORANGE) + scales
    if variety == "shiro_utsuri":
        return BLACK, "#3a3a44", patches(rng, rng.randint(2, 4), WHITE)
    # asagi
    return ASAGI_BLUE, "#ef9a63", flanks(rng, ORANGE) + [
        '<path d="M14 0H-12" stroke="#47627a" stroke-width="1.6" stroke-linecap="round"/>'
    ]


# ---------------------------------------------------------------------------
# The pond


# Creatures
#
# Every creature is drawn in its own coordinates with the nose at +x and moves
# along a closed path with animateMotion. Each draw function returns
# (layer, shadow, body): the layer decides what it swims above or below.


def travel(rng, speed, edge=EDGE, rotate=True):
    """An animateMotion element along a fresh loop, and how long one lap takes."""
    path, length = swim_path(rng, edge)
    duration = length / speed
    start = -rng.uniform(0, duration)
    heading = ' rotate="auto"' if rotate else ""
    return (
        f'<animateMotion dur="{num(duration)}s" begin="{num(start)}s" repeatCount="indefinite"{heading} path="{path}"/>',
        duration,
    )


def sway(values, duration, pivot="0 0", kind="rotate", begin=0):
    """An animateTransform that eases between `values` and back."""
    steps = ";".join(f"{v} {pivot}" if kind == "rotate" else str(v) for v in (*values, values[0]))
    return (
        f'<animateTransform attributeName="transform" type="{kind}" dur="{num(duration)}s" '
        f'begin="{num(begin)}s" repeatCount="indefinite" values="{steps}"/>'
    )


def koi_svg(login, number, size):
    rng = random.Random(f"koi:{login}:{number}")
    variety = "asagi" if number == 0 else rng.choices(*zip(*VARIETIES))[0]
    base, fin, markings = coat(variety, rng)
    path, length = swim_path(rng)

    # Size comes from the koi's tier, give or take a little so a tier is not uniform.
    scale = size * rng.uniform(0.93, 1.07)
    # Small koi dart about a little faster than big ones.
    speed = rng.uniform(13, 26) * (1.2 - 0.2 * scale)
    duration = length / speed
    start = -rng.uniform(0, duration)
    beat = rng.uniform(0.7, 1.1) * (0.8 + 0.25 * scale)
    opacity = rng.uniform(0.82, 1.0)
    tail_x, tail_y = TAIL_JOINT

    motion = (
        f'<animateMotion dur="{num(duration)}s" begin="{num(start)}s" '
        f'repeatCount="indefinite" rotate="auto" path="{path}"/>'
    )
    wag = (
        f'<animateTransform attributeName="transform" type="rotate" dur="{num(beat)}s" '
        f'repeatCount="indefinite" values="-14 {tail_x} {tail_y};14 {tail_x} {tail_y};-14 {tail_x} {tail_y}"/>'
    )
    paddle = (
        f'<animateTransform attributeName="transform" type="rotate" dur="{num(beat * 2)}s" '
        f'repeatCount="indefinite" values="0 8 0;-12 8 0;0 8 0"/>'
    )
    # The shadow follows the same path in a layer offset down and to the right,
    # so it falls the same way whichever way the fish is facing.
    shadow = (
        f'<g>{motion}<g transform="scale({num(scale)})">'
        f'<use href="#koi-body"/><path d="{TAIL}"/></g></g>'
    )
    fish = (
        f'<g opacity="{num(opacity)}">{motion}<g transform="scale({num(scale)})">'
        f'<g fill="{fin}" fill-opacity="0.7"><path d="{TAIL}">{wag}</path>'
        f'<g>{paddle}<ellipse cx="7.5" cy="-6.5" rx="4.2" ry="2" transform="rotate(-38 7.5 -6.5)"/>'
        f'<ellipse cx="7.5" cy="6.5" rx="4.2" ry="2" transform="rotate(38 7.5 6.5)"/></g></g>'
        f'<use href="#koi-body" fill="{base}"/>'
        f'<g clip-path="url(#koi-clip)">{"".join(markings)}</g>'
        f'<use href="#koi-body" fill="url(#koi-sheen)" stroke="#0a2730" stroke-opacity="0.35" stroke-width="0.6"/>'
        f'<circle cx="12.6" cy="-3.3" r="0.95" fill="#111"/><circle cx="12.6" cy="3.3" r="0.95" fill="#111"/>'
        f"</g></g>"
    )
    return "mid", shadow, fish


FROG_SKINS = [
    # body, back spots, eye
    ("#5fae4a", "#3d7a33", "#f2bd3c"),  # green tree frog
    ("#9bd13c", "#5d9a22", "#e0452c"),  # red-eyed tree frog
    ("#2f6fd0", "#14202e", "#f2bd3c"),  # blue poison dart frog
    ("#f2bd3c", "#b5791c", "#1c1c24"),  # golden frog
    ("#7a9a3a", "#4a5f1f", "#f2bd3c"),  # bullfrog
]


def frog_svg(login, number, size):
    """A frog on its own lily pad that breathes, croaks and hops in place."""
    rng = random.Random(f"frog:{login}:{number}")
    x, y = rng.uniform(70, WIDTH - 70), rng.uniform(46, HEIGHT - 52)
    pad = lily_pad(rng, x, y, flower=False)
    body, spot, eye = rng.choice(FROG_SKINS)
    angle = rng.uniform(0, 360)
    period = rng.uniform(4.5, 8)
    begin = -rng.uniform(0, period)
    spots = "".join(
        f'<circle cx="{num(rng.uniform(-5, 4))}" cy="{num(rng.uniform(-3.8, 3.8))}" r="{num(rng.uniform(0.9, 1.6))}" fill="{spot}"/>'
        for _ in range(rng.randint(3, 5))
    )
    legs = ""
    for side in (-1, 1):
        legs += (
            f'<path d="M-3 {4 * side}C-9 {6 * side}-11 {11 * side}-7 {12 * side}C-5 {12 * side}-4 {9 * side}-1 {7 * side}Z" fill="{body}"/>'
            f'<ellipse cx="4.5" cy="{7 * side}" rx="3.2" ry="1.5" transform="rotate({30 * side} 4.5 {7 * side})" fill="{body}"/>'
        )
    hop = (
        f'<animateTransform attributeName="transform" type="scale" dur="{num(period)}s" begin="{num(begin)}s" '
        f'repeatCount="indefinite" keyTimes="0;0.05;0.1;1" values="1;1.35;1;1"/>'
    )
    throat = (
        f'<animate attributeName="ry" values="1.4;2.4;1.4" dur="1.3s" repeatCount="indefinite"/>'
    )
    frog = (
        f'<g transform="translate({num(x)} {num(y)}) rotate({num(angle)}) scale({num(size * rng.uniform(0.95, 1.2))})">'
        f'<ellipse cx="1" cy="1.5" rx="9" ry="7" fill="#02161c" opacity="0.3"/>'
        f"<g>{hop}{legs}"
        f'<ellipse cx="0" cy="0" rx="7.5" ry="5.8" fill="{body}"/>{spots}'
        f'<ellipse cx="6.5" cy="0" rx="4.6" ry="4.4" fill="{body}"/>'
        f'<ellipse cx="9.8" cy="0" rx="1.8" ry="1.4" fill="#f4a3a3" opacity="0.8">{throat}</ellipse>'
        f'<circle cx="8.3" cy="-3.5" r="1.8" fill="{eye}"/><circle cx="8.3" cy="3.5" r="1.8" fill="{eye}"/>'
        f'<circle cx="8.6" cy="-3.5" r="0.8" fill="#111"/><circle cx="8.6" cy="3.5" r="0.8" fill="#111"/>'
        f'<use href="#koi-body" transform="translate(-0.5 0) scale(0.5 0.62)" fill="url(#koi-sheen)"/>'
        "</g></g>"
    )
    return "pads", "", pad + frog


DRAGONFLY_COLORS = ["#2f9fd0", "#d94a3a", "#2fb58a", "#8a56c9", "#e0a82c"]


def dragonfly_svg(login, number, size):
    """A dragonfly that zips around above the water on fast, humming wings."""
    rng = random.Random(f"dragonfly:{login}:{number}")
    color = rng.choice(DRAGONFLY_COLORS)
    motion, _ = travel(rng, rng.uniform(55, 85), edge=30)
    flutter = (
        f'<animateTransform attributeName="transform" type="scale" dur="0.14s" repeatCount="indefinite" '
        f'values="1 1;1 0.55;1 1"/>'
    )
    wings = "".join(
        f'<ellipse cx="{cx}" cy="{side * 6.5}" rx="6.2" ry="1.9" transform="rotate({side * tilt} {cx} {side * 4})" '
        f'fill="#e8fbff" fill-opacity="0.55" stroke="#bfeaf0" stroke-width="0.4"/>'
        for side in (-1, 1)
        for cx, tilt in ((3, 14), (-2, -10))
    )
    shape = (
        '<ellipse cx="3" cy="0" rx="4" ry="1.7"/><circle cx="8" cy="0" r="2.1"/>'
        '<path d="M-1 0H-15" stroke-width="1.6" stroke-linecap="round" stroke="#000"/>'
    )
    shadow = f'<g>{motion}<g transform="scale({num(size)})">{shape}</g></g>'
    body = (
        f'<g>{motion}<g transform="scale({num(size)})">'
        f"<g>{flutter}{wings}</g>"
        f'<path d="M-1 0H-15" stroke="{color}" stroke-width="1.6" stroke-linecap="round"/>'
        f'<path d="M-4 0H-14" stroke="#fff" stroke-opacity="0.35" stroke-width="0.5" stroke-dasharray="1 1.6"/>'
        f'<ellipse cx="3" cy="0" rx="4" ry="1.7" fill="{color}"/>'
        f'<circle cx="8" cy="0" r="2.1" fill="{color}"/><circle cx="9" cy="-1" r="0.8" fill="#1c1c24"/>'
        f'<circle cx="9" cy="1" r="0.8" fill="#1c1c24"/>'
        "</g></g>"
    )
    return "air", shadow, body


TURTLE_SKINS = [
    ("#4a7a3a", "#8fb86a"),
    ("#5a6b34", "#a3b66a"),
    ("#6b5a3a", "#b49a6a"),
    ("#2f6a5a", "#7cc0a0"),
]


def turtle_svg(login, number, size):
    """A slow turtle that paddles along with a patterned shell."""
    rng = random.Random(f"turtle:{login}:{number}")
    shell, skin = rng.choice(TURTLE_SKINS)
    scale = size * rng.uniform(0.95, 1.25)
    motion, duration = travel(rng, rng.uniform(5, 9))
    beat = rng.uniform(2.2, 3.2)
    flippers = ""
    for side in (-1, 1):
        for cx, cy, rx, ry, tilt, pivot, phase in (
            (9.5, 9.5, 6.5, 2.7, 35, (7, 8), 0),
            (-8, 8, 4.5, 2.2, 25, (-7, 7), beat / 2),
        ):
            px, py = pivot
            flippers += (
                f'<g><animateTransform attributeName="transform" type="rotate" dur="{num(beat)}s" '
                f'begin="{num(-phase)}s" repeatCount="indefinite" '
                f'values="{-16 * side} {px} {py * side};{16 * side} {px} {py * side};{-16 * side} {px} {py * side}"/>'
                f'<ellipse cx="{cx}" cy="{cy * side}" rx="{rx}" ry="{ry}" '
                f'transform="rotate({tilt * side} {cx} {cy * side})" fill="{skin}"/></g>'
            )
    scutes = (
        '<path d="M-4.5-4.5L4.5-4.5L7.5 0L4.5 4.5L-4.5 4.5L-7.5 0Z" fill="#fff" fill-opacity="0.12" '
        'stroke="#1b3a14" stroke-opacity="0.5" stroke-width="0.8"/>'
        '<path d="M4.5-4.5L9-8.5M4.5 4.5L9 8.5M-4.5-4.5L-9-8.5M-4.5 4.5L-9 8.5M7.5 0L13 0M-7.5 0L-13 0" '
        'stroke="#1b3a14" stroke-opacity="0.5" stroke-width="0.8" fill="none"/>'
    )
    shadow = (
        f'<g>{motion}<g transform="scale({num(scale)})"><ellipse rx="14" ry="11"/>'
        f'<ellipse cx="16" rx="5.5" ry="4"/></g></g>'
    )
    body = (
        f'<g>{motion}<g transform="scale({num(scale)})">'
        f'{flippers}<path d="M-13 0L-19 -1.4L-19 1.4Z" fill="{skin}"/>'
        f'<ellipse cx="15.5" cy="0" rx="5.5" ry="3.9" fill="{skin}"/>'
        f'<circle cx="17.5" cy="-2" r="0.8" fill="#111"/><circle cx="17.5" cy="2" r="0.8" fill="#111"/>'
        f'<ellipse cx="0" cy="0" rx="13.5" ry="10.8" fill="{shell}" stroke="#1b3a14" stroke-opacity="0.5" stroke-width="0.8"/>'
        f"{scutes}"
        f'<ellipse cx="0" cy="0" rx="13.5" ry="10.8" fill="url(#koi-sheen)"/>'
        "</g></g>"
    )
    return "mid", shadow, body


DUCK_COATS = [
    # body, wing, head, bill   (mallard, mandarin, white, dusk)
    ("#a79a86", "#5d5446", "#1f7a52", "#f2bd3c"),
    ("#c2723a", "#2a4d7a", "#3a8a6a", "#e0452c"),
    ("#f8f4ec", "#d8d2c4", "#f8f4ec", "#ec7a32"),
    ("#4a4f5c", "#2c303a", "#2a3a5a", "#d0a040"),
]


def duck_svg(login, number, size):
    """A duck gliding on the surface, trailing a faint wake."""
    rng = random.Random(f"duck:{login}:{number}")
    body_c, wing_c, head_c, bill_c = rng.choice(DUCK_COATS)
    scale = size * rng.uniform(0.95, 1.15)
    motion, _ = travel(rng, rng.uniform(12, 19))
    bob = sway(("1 1", "1.02 0.96"), rng.uniform(1.6, 2.4), kind="scale")
    wake = (
        '<path d="M-10-5.5L-24-10M-10 5.5L-24 10" stroke="#d8f3f5" stroke-width="1.2" fill="none" '
        'stroke-linecap="round" stroke-opacity="0.22"/>'
    )
    shape = '<ellipse rx="11.5" ry="7"/><circle cx="10.5" r="4.4"/>'
    shadow = f'<g>{motion}<g transform="scale({num(scale)})">{shape}</g></g>'
    body = (
        f'<g>{motion}<g transform="scale({num(scale)})">{wake}<g>{bob}'
        f'<path d="M-10-3.5L-17 0L-10 3.5Z" fill="{wing_c}"/>'
        f'<ellipse rx="11.5" ry="7" fill="{body_c}"/>'
        f'<ellipse cx="-1.5" rx="6.8" ry="5.2" fill="{wing_c}"/>'
        f'<path d="M-6-3H3M-6 0H4M-6 3H3" stroke="#fff" stroke-opacity="0.25" stroke-width="0.7"/>'
        f'<circle cx="10.5" r="4.4" fill="{head_c}"/>'
        f'<ellipse cx="15.8" rx="3.2" ry="2.3" fill="{bill_c}"/>'
        f'<circle cx="11.6" cy="-2.3" r="0.75" fill="#111"/><circle cx="11.6" cy="2.3" r="0.75" fill="#111"/>'
        f'<ellipse rx="11.5" ry="7" fill="url(#koi-sheen)"/>'
        "</g></g></g>"
    )
    return "surface", shadow, body


JELLY_COLORS = [("#f59bd0", "#ffd3ec"), ("#8fe9ff", "#d8f8ff"), ("#b69cff", "#e2d6ff"), ("#9ff0b8", "#dcffe8")]


def jellyfish_svg(login, number, size):
    """A glowing jellyfish that pulses its bell and drifts with trailing tentacles."""
    rng = random.Random(f"jellyfish:{login}:{number}")
    color, light = rng.choice(JELLY_COLORS)
    scale = size * rng.uniform(0.9, 1.4)
    motion, _ = travel(rng, rng.uniform(5, 10), rotate=False)
    pulse = sway(("1 1", "1.14 0.86"), rng.uniform(1.8, 2.8), kind="scale", begin=-rng.uniform(0, 2))
    tentacles = ""
    for x in (-6, -3, 0, 3, 6):
        a = f"M{x} 2C{x - 3} 9 {x + 3} 15 {x} 23"
        b = f"M{x} 2C{x + 3} 9 {x - 3} 15 {x} 23"
        tentacles += (
            f'<path d="{a}" stroke="{color}" stroke-width="1.1" fill="none" stroke-linecap="round" stroke-opacity="0.7">'
            f'<animate attributeName="d" values="{a};{b};{a}" dur="{num(rng.uniform(2.2, 3.4))}s" repeatCount="indefinite"/></path>'
        )
    body = (
        f'<g opacity="0.85">{motion}<g transform="scale({num(scale)})">'
        f'<circle r="22" fill="{light}" opacity="0.08"/>'
        f"{tentacles}<g>{pulse}"
        f'<path d="M-11 1C-11-13 11-13 11 1C8-1 4-2 0-2C-4-2-8-1-11 1Z" fill="{color}" fill-opacity="0.55" '
        f'stroke="{light}" stroke-width="0.8"/>'
        f'<circle cx="0" cy="-4" r="3.6" fill="{light}" fill-opacity="0.5"/>'
        '<circle cx="-4" cy="-5" r="0.9" fill="#fff" fill-opacity="0.8"/>'
        '<circle cx="4" cy="-5" r="0.9" fill="#fff" fill-opacity="0.8"/>'
        "</g></g></g>"
    )
    return "deep", "", body


SHARK_BODY = "M38 0C32-4.5 20-8 6-8.5C-10-9-24-5-34-1.5L-34 1.5C-24 5-10 9 6 8.5C20 8 32 4.5 38 0Z"
SHARK_TAIL = "M-32 0C-38-2-44-8-54-14C-50-7-48-3-47 0C-48 3-50 7-54 14C-44 8-38 2-32 0Z"
SHARK_FIN = "M14 6.5C10 16 2 24-8 27C-5 18-3 11-1 6.5Z"

SHARK_KINDS = [
    # name, back, fins
    ("great white", "#62798b", "#4a5f70"),
    ("tiger", "#6c8893", "#4b6670"),
    ("hammerhead", "#7a8289", "#5f676e"),
]


def shark_svg(login, number, size):
    """A shark that cruises the deep: great white, striped tiger, or hammerhead."""
    rng = random.Random(f"shark:{login}:{number}")
    kind, back, fin = SHARK_KINDS[number % len(SHARK_KINDS)]
    scale = size * rng.uniform(0.95, 1.2)
    motion, _ = travel(rng, rng.uniform(30, 44), edge=40)
    beat = rng.uniform(1.0, 1.5)
    wag = sway((-10, 10), beat, pivot="-32 0")
    extras = ""
    if kind == "tiger":
        extras = '<g clip-path="url(#shark-clip)" fill="#10202a" fill-opacity="0.3">' + "".join(
            f'<path d="M{x} -10L{x + 2.6} -10L{x - 2} 10L{x - 4.6} 10Z"/>' for x in range(-24, 28, 9)
        ) + "</g>"
    head = ""
    if kind == "hammerhead":
        head = (
            f'<ellipse cx="33.5" cy="0" rx="4.4" ry="11.8" fill="{back}"/>'
            '<circle cx="34" cy="-10.6" r="1" fill="#111"/><circle cx="34" cy="10.6" r="1" fill="#111"/>'
        )
    else:
        head = '<circle cx="28" cy="-4.2" r="0.9" fill="#111"/><circle cx="28" cy="4.2" r="0.9" fill="#111"/>'
    body = (
        f'<g opacity="0.92">{motion}<g transform="scale({num(scale)})">'
        f'<path d="{SHARK_TAIL}" fill="{fin}">{wag}</path>'
        f'<path d="{SHARK_FIN}" fill="{fin}"/><path d="{SHARK_FIN}" fill="{fin}" transform="scale(1 -1)"/>'
        f'<path d="M-16 4.5L-24 9L-22 3Z M-16 -4.5L-24 -9L-22 -3Z" fill="{fin}"/>'
        f'<use href="#shark-body" fill="{back}"/>{extras}'
        f'<ellipse cx="-3" rx="11" ry="1.5" fill="#10202a" fill-opacity="0.35"/>'
        f'<path d="M19-5L18-2M16-5.5L15-2.5M13-5.8L12-2.8M19 5L18 2M16 5.5L15 2.5M13 5.8L12 2.8" '
        f'stroke="#10202a" stroke-opacity="0.4" stroke-width="0.7"/>'
        f'<use href="#shark-body" fill="url(#koi-sheen)"/>{head}'
        "</g></g>"
    )
    return "deep", "", body


MANTA_BODY = "M24 0C14-6 4-14-6-28C-10-14-14-6-20-3L-34 0L-20 3C-14 6-10 14-6 28C4 14 14 6 24 0Z"


def manta_svg(login, number, size):
    """A manta ray that flaps its wings and glides on long loops."""
    rng = random.Random(f"manta:{login}:{number}")
    scale = size * rng.uniform(0.95, 1.2)
    motion, _ = travel(rng, rng.uniform(16, 24), edge=40)
    flap = sway(("1 1", "1 0.78"), rng.uniform(2.8, 3.8), kind="scale")
    spots = "".join(
        f'<circle cx="{num(rng.uniform(-8, 14))}" cy="{num(rng.uniform(-9, 9))}" r="{num(rng.uniform(0.7, 1.3))}" fill="#e8f3ff" fill-opacity="0.55"/>'
        for _ in range(9)
    )
    body = (
        f'<g opacity="0.92">{motion}<g transform="scale({num(scale)})">'
        '<path d="M-33 0H-58" stroke="#1d2f45" stroke-width="1.5" stroke-linecap="round"/>'
        f"<g>{flap}"
        '<ellipse cx="23.5" cy="-4.5" rx="4" ry="1.8" transform="rotate(-25 23.5 -4.5)" fill="#1d2f45"/>'
        '<ellipse cx="23.5" cy="4.5" rx="4" ry="1.8" transform="rotate(25 23.5 4.5)" fill="#1d2f45"/>'
        f'<path d="{MANTA_BODY}" fill="#2b4562"/>{spots}'
        f'<path d="{MANTA_BODY}" fill="url(#koi-sheen)"/>'
        "</g></g></g>"
    )
    return "deep", "", body


WHALE_BODY = "M60 0C55-7 35-11.5 10-12.5C-20-12.5-45-7-64-2L-64 2C-45 7-20 12.5 10 12.5C35 11.5 55 7 60 0Z"
WHALE_FLUKES = "M-62 0C-68-3-78-9-92-13C-86-5-83-1.5-83 0C-83 1.5-86 5-92 13C-78 9-68 3-62 0Z"
WHALE_FLIPPER = "M24 10C18 22 8 31-6 35C-4 25 0 16 6 10Z"


def whale_svg(login, number, size):
    """A great whale with long flippers, slow flukes and a spout of mist."""
    rng = random.Random(f"whale:{login}:{number}")
    scale = size * rng.uniform(0.95, 1.1)
    motion, _ = travel(rng, rng.uniform(9, 14), edge=60)
    wag = sway((-6, 6), rng.uniform(3.5, 4.5), pivot="-62 0")
    patches = "".join(
        f'<ellipse cx="{num(rng.uniform(-40, 40))}" cy="{num(rng.uniform(-8, 8))}" rx="{num(rng.uniform(3, 7))}" '
        f'ry="{num(rng.uniform(1.5, 3))}" fill="#8fb3cc" fill-opacity="0.3"/>'
        for _ in range(10)
    )
    period = rng.uniform(8, 12)
    mist = "".join(
        f'<circle cx="38" cy="0" r="3" fill="none" stroke="#e6f6f8" stroke-width="1.6" opacity="0">'
        f'<animate attributeName="r" values="3;18" dur="{num(period)}s" begin="{num(-rng.uniform(0, period) - off)}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="0.6;0" dur="{num(period)}s" begin="{num(-off)}s" repeatCount="indefinite"/></circle>'
        for off in (0, period / 2)
    )
    body = (
        f'<g opacity="0.93">{motion}<g transform="scale({num(scale)})">'
        f'<path d="{WHALE_FLUKES}" fill="#2f5273">{wag}</path>'
        f'<path d="{WHALE_FLIPPER}" fill="#2f5273"/><path d="{WHALE_FLIPPER}" fill="#2f5273" transform="scale(1 -1)"/>'
        f'<use href="#whale-body" fill="#3c6385"/>{patches}'
        f'<ellipse cx="-34" rx="5" ry="1.2" fill="#243f58"/>'
        f'<use href="#whale-body" fill="url(#koi-sheen)"/>'
        f'<circle cx="34" cy="-1.6" r="0.9" fill="#1c3246"/><circle cx="34" cy="1.6" r="0.9" fill="#1c3246"/>'
        f"{mist}</g></g>"
    )
    return "deep", "", body


def kraken_svg(login, number, size):
    """A kraken: glowing eyes, a domed mantle and eight tentacles that never stop writhing."""
    rng = random.Random(f"kraken:{login}:{number}")
    scale = size * rng.uniform(1.7, 2.1)
    motion, _ = travel(rng, rng.uniform(13, 20), edge=60)
    hue = rng.choice([("#6a2a6e", "#9a4a9e", "#f2bd3c"), ("#7a2a3a", "#b04a5a", "#7cf0e6"), ("#2a3a7a", "#4a62b0", "#ff8a4a")])
    mantle, light, eye = hue
    tentacles = ""
    for index in range(8):
        y = -8 + index * 16 / 7
        reach = rng.uniform(26, 36)
        a = f"M-4 {y:.1f}C-12 {y + 7:.1f} -20 {y - 7:.1f} {-reach} {y + 2:.1f}"
        b = f"M-4 {y:.1f}C-12 {y - 7:.1f} -20 {y + 7:.1f} {-reach} {y - 3:.1f}"
        tentacles += (
            f'<path d="{a}" stroke="{mantle}" stroke-width="3.4" fill="none" stroke-linecap="round">'
            f'<animate attributeName="d" values="{a};{b};{a}" dur="{num(rng.uniform(1.8, 3))}s" '
            f'begin="{num(-rng.uniform(0, 3))}s" repeatCount="indefinite"/></path>'
            f'<path d="{a}" stroke="{light}" stroke-width="0.8" fill="none" stroke-dasharray="0.1 3.4" stroke-linecap="round">'
            f'<animate attributeName="d" values="{a};{b};{a}" dur="{num(rng.uniform(1.8, 3))}s" repeatCount="indefinite"/></path>'
        )
    spots = "".join(
        f'<circle cx="{num(rng.uniform(-4, 12))}" cy="{num(rng.uniform(-6, 6))}" r="{num(rng.uniform(0.8, 1.6))}" fill="{light}" fill-opacity="0.6"/>'
        for _ in range(7)
    )
    body = (
        f'<g opacity="0.95">{motion}<g transform="scale({num(scale)})">'
        f"{tentacles}"
        f'<ellipse cx="4" rx="13" ry="9.5" fill="{mantle}"/>{spots}'
        f'<ellipse cx="4" rx="13" ry="9.5" fill="url(#koi-sheen)"/>'
        f'<circle cx="13.5" cy="-5" r="2.5" fill="{eye}"><animate attributeName="opacity" values="1;0.55;1" dur="2.6s" repeatCount="indefinite"/></circle>'
        f'<circle cx="13.5" cy="5" r="2.5" fill="{eye}"><animate attributeName="opacity" values="1;0.55;1" dur="2.6s" repeatCount="indefinite"/></circle>'
        '<circle cx="14.2" cy="-5" r="1" fill="#111"/><circle cx="14.2" cy="5" r="1" fill="#111"/>'
        "</g></g>"
    )
    return "deep", "", body


SERPENT_COLORS = {
    # body, stripe, spine, eye, whisker
    "sea_serpent": ("#2e9d8f", "#1f7a73", "#bff0ee", "#ffe27a", "#8fe8dc"),
    "golden_dragon": ("#f2bd3c", "#d9822b", "#fff3c4", "#ff4a3a", "#fff3c4"),
}


def serpent_svg(login, number, size, kind):
    """A long chain of segments that all follow the head along one winding path."""
    rng = random.Random(f"{kind}:{login}:{number}")
    body_c, stripe_c, spine_c, eye_c, whisker_c = SERPENT_COLORS[kind]
    scale = size * rng.uniform(1.0, 1.15)
    speed = rng.uniform(26, 36)
    path, length = swim_path(rng, 40)
    duration = length / speed
    start = -rng.uniform(0, duration)
    count = 20
    spacing = 6.5 * scale
    delay = spacing / speed
    segments = []
    for index in reversed(range(count)):
        t = index / (count - 1)
        half = (7.2 - 5.4 * t**0.8) * scale
        move = (
            f'<animateMotion dur="{num(duration)}s" begin="{num(start - index * delay)}s" '
            f'repeatCount="indefinite" rotate="auto" path="{path}"/>'
        )
        color = body_c if index % 2 == 0 else stripe_c
        parts = []
        if index % 3 == 2 and index < count - 3:
            fin = f"{half + 2.4 * scale:.1f}"
            parts.append(
                f'<ellipse cx="-1" cy="-{fin}" rx="{num(3.6 * scale)}" ry="{num(1.3 * scale)}" transform="rotate(-28 -1 -{fin})" fill="{stripe_c}" fill-opacity="0.85"/>'
                f'<ellipse cx="-1" cy="{fin}" rx="{num(3.6 * scale)}" ry="{num(1.3 * scale)}" transform="rotate(28 -1 {fin})" fill="{stripe_c}" fill-opacity="0.85"/>'
            )
        parts.append(f'<ellipse rx="{num(spacing * 0.95)}" ry="{num(half)}" fill="{color}"/>')
        parts.append(f'<circle r="{num(max(0.9, half * 0.28))}" fill="{spine_c}" fill-opacity="0.8"/>')
        if index == 0:
            whisker_a = "M11 2C18 9 24 3 32 10"
            whisker_b = "M11 2C18 -1 24 9 32 4"
            horns = (
                f'<path d="M1-5C-5-11-11-11-15-8M1 5C-5 11-11 11-15 8" stroke="{stripe_c}" stroke-width="1.8" '
                f'fill="none" stroke-linecap="round"/>'
            )
            whiskers = "".join(
                f'<g transform="scale(1 {flip})"><path d="{whisker_a}" stroke="{whisker_c}" stroke-width="0.9" fill="none" '
                f'stroke-linecap="round" stroke-opacity="0.85"><animate attributeName="d" values="{whisker_a};{whisker_b};{whisker_a}" '
                f'dur="{num(rng.uniform(1.6, 2.4))}s" repeatCount="indefinite"/></path></g>'
                for flip in (1, -1)
            )
            parts = [
                f'<g transform="scale({num(scale)})">{whiskers}{horns}'
                f'<ellipse rx="11" ry="7.6" fill="{body_c}"/><ellipse cx="9.5" rx="5" ry="4.4" fill="{body_c}"/>'
                f'<ellipse rx="11" ry="7.6" fill="url(#koi-sheen)"/>'
                f'<circle cx="6" cy="-4.4" r="1.7" fill="{eye_c}"/><circle cx="6" cy="4.4" r="1.7" fill="{eye_c}"/>'
                f'<circle cx="6.4" cy="-4.4" r="0.7" fill="#111"/><circle cx="6.4" cy="4.4" r="0.7" fill="#111"/>'
                f'<circle cx="12.5" cy="-1.6" r="0.6" fill="#111"/><circle cx="12.5" cy="1.6" r="0.6" fill="#111"/></g>'
            ]
        segments.append(f"<g>{move}{''.join(parts)}</g>")
    return "deep", "", f'<g opacity="0.95">{"".join(segments)}</g>'


CREATURES = {
    "koi": koi_svg,
    "frog": frog_svg,
    "dragonfly": dragonfly_svg,
    "turtle": turtle_svg,
    "duck": duck_svg,
    "jellyfish": jellyfish_svg,
    "shark": shark_svg,
    "manta": manta_svg,
    "whale": whale_svg,
    "kraken": kraken_svg,
    "sea_serpent": lambda login, number, size: serpent_svg(login, number, size, "sea_serpent"),
    "golden_dragon": lambda login, number, size: serpent_svg(login, number, size, "golden_dragon"),
}


def lily_pad(rng, x, y, flower=True):
    radius = rng.uniform(17, 28)
    notch = rng.uniform(0, 360)
    half_gap = math.radians(14)
    a1 = math.radians(notch) + half_gap
    a2 = math.radians(notch) - half_gap + math.tau
    p1 = (x + radius * math.cos(a1), y + radius * math.sin(a1))
    p2 = (x + radius * math.cos(a2), y + radius * math.sin(a2))
    sway = rng.uniform(2, 4)
    parts = [
        f'<g><animateTransform attributeName="transform" type="rotate" dur="{num(rng.uniform(7, 11))}s" '
        f'repeatCount="indefinite" values="{-sway} {num(x)} {num(y)};{sway} {num(x)} {num(y)};{-sway} {num(x)} {num(y)}"/>',
        f'<ellipse cx="{num(x + 2)}" cy="{num(y + 3)}" rx="{num(radius)}" ry="{num(radius)}" fill="#04212a" opacity="0.35"/>',
        f'<path d="M{num(x)} {num(y)}L{num(p1[0])} {num(p1[1])}'
        f'A{num(radius)} {num(radius)} 0 1 1 {num(p2[0])} {num(p2[1])}Z" fill="#3f8f47" stroke="#2c6e36" stroke-width="1"/>',
    ]
    for vein in range(5):
        angle = math.radians(notch + 50 + vein * 52)
        parts.append(
            f'<path d="M{num(x)} {num(y)}L{num(x + radius * 0.8 * math.cos(angle))} '
            f'{num(y + radius * 0.8 * math.sin(angle))}" stroke="#57a85d" stroke-width="0.8" opacity="0.7"/>'
        )
    if flower and rng.random() < 0.5:
        fx, fy = x + rng.uniform(-6, 6), y + rng.uniform(-6, 6)
        for petal in range(6):
            angle = petal * 60 + rng.uniform(-8, 8)
            parts.append(
                f'<ellipse cx="{num(fx)}" cy="{num(fy - 4)}" rx="2.6" ry="5.2" fill="#f6b7cf" '
                f'transform="rotate({num(angle)} {num(fx)} {num(fy)})"/>'
            )
        parts.append(f'<circle cx="{num(fx)}" cy="{num(fy)}" r="2.6" fill="#f7d34a"/>')
    parts.append("</g>")
    return "".join(parts)


def ripple(rng):
    x, y = rng.uniform(60, WIDTH - 60), rng.uniform(40, HEIGHT - 40)
    duration = rng.uniform(4.5, 7)
    begin = -rng.uniform(0, duration)
    return (
        f'<circle cx="{num(x)}" cy="{num(y)}" r="2" fill="none" stroke="#d8f3f5" stroke-width="1.2">'
        f'<animate attributeName="r" values="2;30" dur="{num(duration)}s" begin="{num(begin)}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="0.5;0" dur="{num(duration)}s" begin="{num(begin)}s" repeatCount="indefinite"/>'
        "</circle>"
    )


LAYER_ORDER = ["deep", "mid", "pads", "surface", "air"]
# How far each layer's shadow falls on the water, and how faint it is.
SHADOWS = {"mid": ((5, 8), 0.28), "surface": ((4, 7), 0.25), "air": ((14, 22), 0.16)}


def pond_svg(login, contributions, caption=True):
    creatures, next_kind, needed = roster(contributions)
    count = len(creatures)
    layers = {name: [] for name in LAYER_ORDER}
    shadows = {name: [] for name in SHADOWS}
    # Newest (biggest) first, so the older and smaller creatures swim over them.
    for kind, number, size in reversed(creatures):
        layer, shadow, body = CREATURES[kind](login, number, size)
        layers[layer].append(body)
        if shadow:
            shadows[layer].append(shadow)
    rng = random.Random(f"pond:{login}")

    pebbles = []
    for _ in range(55):
        shade = rng.choice(("#0f4c5a", "#1a6472", "#0a3a46", "#2a7480"))
        pebbles.append(
            ellipse(rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT), rng.uniform(2, 7), rng.uniform(1.5, 4.5),
                    rng.uniform(0, 180), shade)
        )
    light = []
    for _ in range(4):
        duration = rng.uniform(9, 14)
        light.append(
            f'<ellipse cx="{num(rng.uniform(100, WIDTH - 100))}" cy="{num(rng.uniform(60, HEIGHT - 60))}" '
            f'rx="{num(rng.uniform(90, 180))}" ry="{num(rng.uniform(30, 60))}" fill="#bff0ee" opacity="0.04">'
            f'<animate attributeName="opacity" values="0.02;0.08;0.02" dur="{num(duration)}s" '
            f'begin="{num(-rng.uniform(0, duration))}s" repeatCount="indefinite"/></ellipse>'
        )
    ripples = [ripple(rng) for _ in range(3)]
    # Pads sit near the edges, clear of the caption in the bottom-right corner.
    pad_spots = [(48, 46), (92, 30), (WIDTH - 60, 44), (70, HEIGHT - 44), (WIDTH * 0.45, 22)]
    pads = [lily_pad(rng, x + rng.uniform(-10, 10), y + rng.uniform(-6, 6)) for x, y in pad_spots]

    def shadow_layer(name):
        (dx, dy), opacity = SHADOWS[name]
        if not shadows[name]:
            return ""
        return f'<g transform="translate({dx} {dy})" fill="#03161c" opacity="{opacity}">{"".join(shadows[name])}</g>'

    text = ""
    plural = "creature" if count == 1 else "creatures"
    if caption:
        noun = "contribution" if contributions == 1 else "contributions"
        label = (
            f"{count} {plural} · {contributions:,} {noun} in the last {WINDOW_DAYS} days"
            f" · {needed:,} more for a {NAMES[next_kind]}"
        )
        text = (
            f'<text x="{WIDTH - 16}" y="{HEIGHT - 14}" text-anchor="end" fill="#d6eff1" '
            'stroke="#08313c" stroke-width="3" stroke-linejoin="round" paint-order="stroke" '
            f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="12">'
            f"{label}</text>"
        )

    title = f"{login}'s pond: {count} {plural}" + (
        f" from {contributions:,} contributions in the last {WINDOW_DAYS} days" if caption else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" '
        f'role="img" aria-label="{title}">'
        f"<title>{title}</title>"
        "<defs>"
        f'<path id="koi-body" d="{BODY}"/>'
        f'<path id="shark-body" d="{SHARK_BODY}"/>'
        f'<path id="whale-body" d="{WHALE_BODY}"/>'
        f'<clipPath id="koi-clip"><path d="{BODY}"/></clipPath>'
        f'<clipPath id="shark-clip"><path d="{SHARK_BODY}"/></clipPath>'
        f'<clipPath id="pond-clip"><rect width="{WIDTH}" height="{HEIGHT}" rx="18"/></clipPath>'
        '<radialGradient id="water" cx="50%" cy="40%" r="75%">'
        '<stop offset="0" stop-color="#1b6f7a"/><stop offset="0.6" stop-color="#11505d"/>'
        '<stop offset="1" stop-color="#08313c"/></radialGradient>'
        '<linearGradient id="koi-sheen" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#000" stop-opacity="0.16"/>'
        '<stop offset="0.42" stop-color="#fff" stop-opacity="0.3"/>'
        '<stop offset="0.62" stop-color="#fff" stop-opacity="0"/>'
        '<stop offset="1" stop-color="#000" stop-opacity="0.16"/></linearGradient>'
        "</defs>"
        '<g clip-path="url(#pond-clip)">'
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#water)"/>'
        f'<g opacity="0.55">{"".join(pebbles)}</g>'
        f'{"".join(light)}'
        # Deep creatures swim under a veil of water, so they read as farther down.
        f'{"".join(layers["deep"])}'
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#0b4552" opacity="0.22"/>'
        f'{shadow_layer("mid")}'
        f'{"".join(layers["mid"])}'
        f'{"".join(ripples)}'
        f'{"".join(pads)}'
        f'{"".join(layers["pads"])}'
        f'{shadow_layer("surface")}'
        f'{"".join(layers["surface"])}'
        f'{shadow_layer("air")}'
        f'{"".join(layers["air"])}'
        f"{text}"
        f'<rect width="{WIDTH}" height="{HEIGHT}" rx="18" fill="none" stroke="#062a33" stroke-width="6"/>'
        "</g></svg>\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--login", required=True, help="GitHub account whose recent contributions fill the pond")
    parser.add_argument("--output", default="pond/pond.svg", help="where to write the SVG")
    parser.add_argument(
        "--contributions", type=int, help="use this 30-day count instead of asking GitHub (for previews)"
    )
    parser.add_argument("--no-caption", action="store_true", help="leave out the count in the corner")
    args = parser.parse_args()

    if args.contributions is not None:
        contributions = args.contributions
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            sys.exit("set GITHUB_TOKEN (the workflow's built-in token is enough), or pass --contributions")
        contributions = recent_contributions(args.login, token)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(pond_svg(args.login, contributions, caption=not args.no_caption), encoding="utf-8")
    print(
        f"{args.login}: {contributions:,} contributions in the last {WINDOW_DAYS} days, "
        f"{len(roster(contributions)[0])} creatures -> {output}"
    )


if __name__ == "__main__":
    main()
