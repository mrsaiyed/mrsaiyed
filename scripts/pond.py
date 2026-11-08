#!/usr/bin/env python3
"""Draw a koi pond for a GitHub profile README.

The pond holds one koi for every so many contributions in the last 30 days, and
always at least one. The first 10 koi are small and take one contribution each,
the next 10 are bigger and take 5 each, and every koi after that is bigger still
and takes 10. As old contributions fall out of the window the newest koi leave
again. Each koi keeps the same colors and swimming path from day to day.

Usage:
    GITHUB_TOKEN=... python3 scripts/pond.py --login octocat --output pond/pond.svg
    python3 scripts/pond.py --login octocat --contributions 25 --output preview.svg

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
# Each tier is (how many koi it holds, contributions per koi, koi size). A tier
# fills before the next one starts, and the last tier never fills. So 10
# contributions make 10 small koi, 60 make 10 small and 10 medium, and 100 add
# 4 large ones.
TIERS = [
    (10, 1, 0.65),
    (10, 5, 1.0),
    (None, 10, 1.45),
]
# A cap so a very busy month still leaves room to see the water.
MAX_FISH = 50

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


def fish_count(contributions):
    """How many koi `contributions` buy, never fewer than one."""
    count, left = 0, contributions
    for capacity, per_fish, _ in TIERS:
        bought = left // per_fish if capacity is None else min(left // per_fish, capacity)
        count += bought
        left -= bought * per_fish
        if capacity is None or bought < capacity:
            break
    return min(MAX_FISH, max(1, count))


def contributions_needed_for(fish):
    """Contributions it takes to have `fish` koi."""
    total, remaining = 0, fish
    for capacity, per_fish, _ in TIERS:
        taken = remaining if capacity is None else min(remaining, capacity)
        total += taken * per_fish
        remaining -= taken
    return total


def fish_size(fish_number):
    """The size of koi number `fish_number` (from 0), from the tier it falls in."""
    for capacity, _, size in TIERS:
        if capacity is None or fish_number < capacity:
            return size
        fish_number -= capacity
    return TIERS[-1][2]


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


def swim_path(rng):
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
        points.append((min(max(x, EDGE), WIDTH - EDGE), min(max(y, EDGE), HEIGHT - EDGE)))
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


def fish_svg(login, fish_number):
    rng = random.Random(f"koi:{login}:{fish_number}")
    variety = "asagi" if fish_number == 0 else rng.choices(*zip(*VARIETIES))[0]
    base, fin, markings = coat(variety, rng)
    path, length = swim_path(rng)

    # Size comes from the koi's tier, give or take a little so a tier is not uniform.
    scale = fish_size(fish_number) * rng.uniform(0.93, 1.07)
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
    return shadow, fish


def lily_pad(rng, x, y):
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
    if rng.random() < 0.5:
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


def pond_svg(login, contributions, caption=True):
    count = fish_count(contributions)
    # Draw the biggest koi first so the small ones swim over them.
    shadows, fishes = zip(*(fish_svg(login, number) for number in reversed(range(count))))
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

    text = ""
    if caption:
        if count >= MAX_FISH:
            progress = "the pond is full"
        else:
            needed = contributions_needed_for(count + 1) - contributions
            progress = f"{needed:,} more for the next koi"
        noun = "contribution" if contributions == 1 else "contributions"
        label = f"{count} koi · {contributions:,} {noun} in the last {WINDOW_DAYS} days · {progress}"
        text = (
            f'<text x="{WIDTH - 16}" y="{HEIGHT - 14}" text-anchor="end" fill="#d6eff1" '
            'stroke="#08313c" stroke-width="3" stroke-linejoin="round" paint-order="stroke" '
            f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif" font-size="12">'
            f"{label}</text>"
        )

    title = f"{login}'s koi pond: {count} koi" + (
        f" from {contributions:,} contributions in the last {WINDOW_DAYS} days" if caption else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" '
        f'role="img" aria-label="{title}">'
        f"<title>{title}</title>"
        "<defs>"
        f'<path id="koi-body" d="{BODY}"/>'
        f'<clipPath id="koi-clip"><path d="{BODY}"/></clipPath>'
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
        f'<g transform="translate(5 8)" fill="#03161c" opacity="0.28">{"".join(shadows)}</g>'
        f'{"".join(fishes)}'
        f'{"".join(ripples)}'
        f'{"".join(pads)}'
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
        f"{fish_count(contributions)} koi -> {output}"
    )


if __name__ == "__main__":
    main()
