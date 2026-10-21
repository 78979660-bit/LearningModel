"""Generate the transparent, paper-like vector artwork used by Quick.

The illustrations stay SVG so Qt can scale them cleanly at different DPI
settings. The generator uses only Python's standard library.
"""
from pathlib import Path
import base64
import math
import random
import struct
import zlib

ASSETS = Path(__file__).resolve().parents[1] / "study_app/quick/qml/assets"


def svg(width, height, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
            + body.replace('href="', 'xlink:href="') + '\n</svg>\n')


def paper():
    """A transparent, seamless tile of fine kozo-like fibers and soft pulp."""
    rng = random.Random(20261004)
    width = height = 960
    # Fine grain is embedded in the SVG so every paper surface still shares
    # one static cached texture. Avoid thousands of additional scene nodes.
    grain_rng = random.Random(20261009)
    pixels = bytearray()
    for _ in range(height):
        pixels.append(0)  # PNG scanline filter.
        for _ in range(width):
            pixels.extend((162, 142, 124, grain_rng.randrange(23)))
    def chunk(kind, payload):
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(pixels, 9)) + chunk(b'IEND', b''))
    grain = '<image width="960" height="960" opacity=".75" href="data:image/png;base64,' + base64.b64encode(png).decode('ascii') + '"/>'
    defs = '''<defs>
 <radialGradient id="warmPulp">
  <stop offset="0" stop-color="#BBA897" stop-opacity=".12"/>
  <stop offset=".18" stop-color="#C4B3A2" stop-opacity=".10"/>
  <stop offset=".36" stop-color="#D2C2B1" stop-opacity=".075"/>
  <stop offset=".55" stop-color="#DDD1C3" stop-opacity=".05"/>
  <stop offset=".74" stop-color="#E9DFD3" stop-opacity=".025"/>
  <stop offset=".9" stop-color="#F8F4EF" stop-opacity=".006"/>
  <stop offset="1" stop-color="#F8F4EF" stop-opacity="0"/>
 </radialGradient>
 <radialGradient id="coolPulp">
  <stop offset="0" stop-color="#FFFFFF" stop-opacity=".24"/>
  <stop offset=".2" stop-color="#FFFFFF" stop-opacity=".20"/>
  <stop offset=".42" stop-color="#FFFFFF" stop-opacity=".15"/>
  <stop offset=".64" stop-color="#FFFFFF" stop-opacity=".09"/>
  <stop offset=".82" stop-color="#FFFFFF" stop-opacity=".035"/>
  <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
 </radialGradient>
</defs>'''
    washes = []
    for index in range(48):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        rx, ry = rng.uniform(36, 116), rng.uniform(24, 78)
        rotation = 0  # Axis-aligned bounds keep the wrapped tile seamless.
        fill = "url(#coolPulp)" if index % 3 else "url(#warmPulp)"
        shifts_x = [0] + ([width] if x - rx < 0 else []) + ([-width] if x + rx > width else [])
        shifts_y = [0] + ([height] if y - ry < 0 else []) + ([-height] if y + ry > height else [])
        for shift_x in shifts_x:
            for shift_y in shifts_y:
                washes.append(
                    f'<ellipse cx="{x + shift_x:.1f}" cy="{y + shift_y:.1f}" '
                    f'rx="{rx:.1f}" ry="{ry:.1f}" transform="rotate({rotation:.1f} '
                    f'{x + shift_x:.1f} {y + shift_y:.1f})" fill="{fill}"/>'
                )

    fibers = []
    # Short interwoven fibers form the sheet; only occasional longer strands
    # catch the light. Keep the same physical tile size at every panel size.
    fields = []
    for _ in range(28):
        fields.append((rng.uniform(0, width), rng.uniform(0, height),
                       rng.uniform(-math.pi, math.pi), rng.uniform(28, 92),
                       rng.uniform(10, 34)))

    for index in range(3200):
        if index < 2000:
            cx, cy, field_angle, spread_along, spread_across = rng.choice(fields)
            along = rng.gauss(0, spread_along)
            across = rng.gauss(0, spread_across)
            x = (cx + along * math.cos(field_angle) - across * math.sin(field_angle)) % width
            y = (cy + along * math.sin(field_angle) + across * math.cos(field_angle)) % height
            angle = field_angle + rng.gauss(0, .7)
        else:
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            angle = rng.uniform(-math.pi, math.pi)
        bucket = index % 10
        if bucket == 0:
            length = rng.uniform(14, 30)
        elif bucket < 5:
            length = rng.uniform(5, 14)
        else:
            length = rng.uniform(1.5, 5.5)
        dx, dy = length * math.cos(angle), length * math.sin(angle)
        normal_x, normal_y = -math.sin(angle), math.cos(angle)
        curve = rng.uniform(.08, .2) * length
        if index % 7 == 0:
            curve *= .32
        side = -1 if rng.random() < .5 else 1
        first_curve = side * curve
        second_curve = first_curve * rng.uniform(-.65, .55)
        control1_x = x + dx * .3 + normal_x * first_curve
        control1_y = y + dy * .3 + normal_y * first_curve
        control2_x = x + dx * .7 + normal_x * second_curve
        control2_y = y + dy * .7 + normal_y * second_curve
        d = (f'M{x:.2f} {y:.2f}C{control1_x:.2f} {control1_y:.2f} '
             f'{control2_x:.2f} {control2_y:.2f} {x + dx:.2f} {y + dy:.2f}')
        xs = (x, control1_x, control2_x, x + dx)
        ys = (y, control1_y, control2_y, y + dy)
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        shifts_x = [0]
        shifts_y = [0]
        if min_x < 1:
            shifts_x.append(width)
        if max_x > width - 1:
            shifts_x.append(-width)
        if min_y < 1:
            shifts_y.append(height)
        if max_y > height - 1:
            shifts_y.append(-height)
        if index % 3 == 0:
            color = "#AB957E"
            opacity = rng.uniform(.08, .18)
        else:
            color = "#FFFFFF" if index % 3 else "#FFFDF9"
            opacity = rng.uniform(.20, .44) if length < 12 else rng.uniform(.28, .50)
        stroke_width = rng.uniform(.28, .58) if length < 12 else rng.uniform(.4, .75)
        for shift_x in shifts_x:
            for shift_y in shifts_y:
                transform = f' transform="translate({shift_x} {shift_y})"' if shift_x or shift_y else ""
                fibers.append(
                    f'<path d="{d}"{transform} stroke="{color}" '
                    f'stroke-width="{stroke_width:.2f}" opacity="{opacity:.2f}"/>'
                )

    pinpricks = []
    for index in range(3000):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        radius = rng.uniform(.3, .7)
        opacity = rng.uniform(.055, .14)
        color = "#AD9985" if index % 3 else "#FFFFFF"
        shifts_x = [0] + ([width] if x - radius < 0 else []) + ([-width] if x + radius > width else [])
        shifts_y = [0] + ([height] if y - radius < 0 else []) + ([-height] if y + radius > height else [])
        for shift_x in shifts_x:
            for shift_y in shifts_y:
                pinpricks.append(
                    f'<circle cx="{x + shift_x:.2f}" cy="{y + shift_y:.2f}" '
                    f'r="{radius:.2f}" fill="{color}" opacity="{opacity:.2f}"/>'
                )

    return svg(width, height, defs + '\n' + grain + '\n<g opacity=".6">' + ''.join(washes) + '</g>\n'
               + '<g fill="none" stroke-linecap="round">' + ''.join(fibers) + '</g>\n'
               + '<g>' + ''.join(pinpricks) + '</g>')


def sakura():
    """A loose, airy blossom branch; floating petals belong to the live scene."""
    defs = '''<defs>
 <radialGradient id="petal" cx="52%" cy="78%" r="86%">
  <stop offset="0" stop-color="#D977A2" stop-opacity=".72"/>
  <stop offset=".27" stop-color="#F1B6CE" stop-opacity=".9"/>
  <stop offset=".76" stop-color="#FFF3F5" stop-opacity=".94"/>
  <stop offset="1" stop-color="#FFF9F8" stop-opacity=".68"/>
 </radialGradient>
 <radialGradient id="bloomMist">
  <stop offset="0" stop-color="#F4C0D6" stop-opacity=".18"/>
  <stop offset="1" stop-color="#F4C0D6" stop-opacity="0"/>
 </radialGradient>
 <radialGradient id="flowerHeart" cx="48%" cy="46%" r="56%">
  <stop offset="0" stop-color="#BD3E68" stop-opacity=".62"/>
  <stop offset=".32" stop-color="#D75E86" stop-opacity=".4"/>
  <stop offset="1" stop-color="#E9A0B8" stop-opacity="0"/>
 </radialGradient>
 <linearGradient id="wood" x1="0" y1="0" x2=".2" y2="1">
  <stop stop-color="#766778"/><stop offset=".46" stop-color="#9A8998"/>
  <stop offset="1" stop-color="#C0AFB6"/>
 </linearGradient>
 <linearGradient id="leaf" x1="0" y1="0" x2="1" y2="1">
  <stop stop-color="#A0A3AC"/><stop offset=".55" stop-color="#8E8AA6"/>
  <stop offset="1" stop-color="#B9A5B8"/>
 </linearGradient>
 <g id="flower">
  <g fill="url(#petal)" stroke="#EAB7CC" stroke-opacity=".38" stroke-width=".52">
   <path d="M0 1C-4-3-10-8-10-15C-11-22-6-26-2-22C2-26 7-23 8-17C9-10 4-4 0 1Z"/>
   <path d="M0 1C-5-3-12-6-13-13C-15-19-11-24-6-22C-1-25 4-22 5-17C8-11 5-4 0 1Z" transform="rotate(69) scale(1.02 .96)"/>
   <path d="M0 1C-5-4-12-8-12-15C-12-21-6-24-2-20C3-24 9-21 10-15C11-9 5-3 0 1Z" transform="rotate(143) scale(.96 1.04)"/>
   <path d="M0 1C-4-3-11-8-10-15C-10-21-5-24-1-21C4-24 9-21 9-15C10-9 4-3 0 1Z" transform="rotate(215) scale(1.04 .95)"/>
   <path d="M0 1C-5-3-12-7-12-14C-13-20-8-24-4-21C0-24 6-22 8-16C10-10 5-4 0 1Z" transform="rotate(291) scale(.95 1.03)"/>
  </g>
  <g fill="none" stroke="#FFFFFF" stroke-opacity=".34" stroke-width=".46" stroke-linecap="round">
   <path d="M-1-3C-4-7-5-11-5-16M1-3C4-7 5-11 4-16M2 0C6-2 8-5 9-9"/>
  </g>
  <ellipse cx="0" cy="-1" rx="10" ry="8" fill="url(#flowerHeart)"/>
  <circle cx="0" cy="-1" r="1.35" fill="#BA426B" fill-opacity=".35"/>
 </g>
 <g id="flowerLoose">
  <g fill="url(#petal)" stroke="#EAB7CC" stroke-opacity=".3" stroke-width=".48">
   <path d="M0 1C-5-4-10-10-8-17C-6-23 0-24 4-19C8-13 5-5 0 1Z"/>
   <path d="M0 1C-3-5-5-12-1-18C3-23 10-21 11-15C12-9 6-3 0 1Z" transform="rotate(78) scale(1.04 .93)"/>
   <path d="M0 1C-6-3-12-8-11-15C-10-22-4-25 1-20C6-15 5-6 0 1Z" transform="rotate(164) scale(.94 1.08)"/>
   <path d="M0 1C-4-4-9-9-7-16C-5-22 1-24 5-19C9-14 5-5 0 1Z" transform="rotate(246) scale(1.05 .96)"/>
  </g>
  <g fill="none" stroke="#FFFFFF" stroke-opacity=".33" stroke-width=".42" stroke-linecap="round">
   <path d="M-1-3C-4-8-4-12-3-17M2-2C5-7 6-11 6-14"/>
  </g>
  <ellipse cx="0" cy="-1" rx="9" ry="7" fill="url(#flowerHeart)"/>
  <circle cx="0" cy="-1" r="1.15" fill="#BA426B" fill-opacity=".28"/>
 </g>
 <g id="flowerSoft">
  <g fill="url(#petal)" fill-opacity=".88" stroke="#FFF8F7" stroke-opacity=".76" stroke-width=".72">
   <path d="M1 1C-4-4-10-9-9-16C-8-21-3-24 1-20C6-24 11-20 10-14C9-8 4-3 1 1Z"/>
   <path d="M1 1C-5-3-11-7-12-14C-12-20-6-23-2-19C3-23 8-20 8-14C8-8 4-2 1 1Z" transform="rotate(73) scale(.93 1.05)"/>
   <path d="M1 1C-4-4-9-9-8-16C-7-21-1-23 3-19C7-22 12-18 10-12C8-7 4-2 1 1Z" transform="rotate(151) scale(1.04 .96)"/>
   <path d="M1 1C-4-3-10-8-9-15C-8-22-2-24 2-19C7-22 12-18 10-12C8-6 4-2 1 1Z" transform="rotate(226) scale(.97 1.03)"/>
   <path d="M1 1C-5-3-10-9-9-15C-8-21-3-23 1-19C6-22 11-18 10-12C9-6 4-2 1 1Z" transform="rotate(302) scale(1.03 .96)"/>
  </g>
  <ellipse cx="0" cy="-1" rx="8.5" ry="6.8" fill="url(#flowerHeart)"/>
  <circle cx="0" cy="-1" r="1.2" fill="#BA426B" fill-opacity=".25"/>
 </g>
 <g id="bud">
  <path d="M0 0C-8-4-10-14-3-18C-2-10 3-8 0 0Z" fill="#C57D9C"/>
  <path d="M0 0C2-9 9-13 13-7C8-7 6-1 0 0Z" fill="#DCA1B6"/>
 </g>
</defs>'''
    branches = '''<g fill="none" stroke="url(#wood)" stroke-linecap="round" stroke-linejoin="round">
 <path stroke-width="6.2" d="M590-18C525 8 486 27 441 45S356 77 314 105S239 151 177 179"/>
 <path stroke-width="3.6" d="M500 16C463 4 431-2 391-4M459 37C480 69 501 99 531 117M410 58C380 29 353 6 321-12M363 75C338 53 303 39 267 38M312 106C318 139 307 175 291 202M262 132C224 115 192 111 155 116M213 161C198 188 170 207 141 216"/>
 <path stroke-width="1.55" d="M534 22L555 61M470 6L450-18M478 80L453 112M421 52L398 91M366 24L343 54M326 79L290 93M282 48L250 74M309 160L274 179M229 124L209 94M177 147L148 165M179 194L155 226"/>
</g>
<g fill="url(#leaf)" opacity=".55">
 <path d="M393 66C369 43 349 41 331 49C350 66 372 72 393 66ZM358 88C380 64 400 58 419 66C401 85 379 94 358 88Z"/>
 <path d="M275 98C251 76 229 73 210 81C232 99 253 105 275 98ZM293 111C314 88 334 84 352 93C334 111 314 118 293 111Z"/>
 <path d="M220 153C198 132 178 129 159 138C180 156 199 161 220 153ZM242 162C263 143 283 141 300 151C282 168 262 173 242 162Z"/>
 <path d="M453 53C474 41 491 43 503 56C484 66 467 66 453 53Z"/>
</g>
<g fill="url(#bloomMist)">
 <circle cx="484" cy="25" r="64"/><circle cx="414" cy="55" r="67"/>
 <circle cx="351" cy="38" r="57"/><circle cx="449" cy="112" r="69"/>
</g>'''
    flowers = [
        (550, 12, 1.08, 18, "flower"), (520, 40, .83, -14, "flowerLoose"),
        (494, 6, .72, 24, "flowerSoft"), (468, 30, .96, 7, "flower"),
        (442, 10, .78, -10, "flowerLoose"), (423, 57, 1.05, 17, "flower"),
        (391, 35, .76, -18, "flowerSoft"), (371, 73, .91, 23, "flower"),
        (344, 22, .62, 14, "flowerLoose"), (479, 91, .94, -8, "flowerSoft"),
        (450, 121, .87, 13, "flower"), (408, 103, .83, -22, "flowerLoose"),
        (387, 142, .74, 15, "flower"), (349, 116, .67, -14, "flowerSoft"),
        (319, 151, .62, 18, "flowerLoose"), (298, 80, .55, -10, "flowerSoft"),
        (285, 182, .5, 18, "flower"), (241, 116, .5, 5, "flowerLoose"),
    ]
    bloom = '\n'.join(f'<use href="#{kind}" transform="translate({x} {y}) rotate({angle}) scale({scale})"/>'
                      for x, y, scale, angle, kind in flowers)
    buds = '\n'.join(f'<use href="#bud" transform="translate({x} {y}) rotate({angle}) scale({scale})"/>'
                     for x, y, angle, scale in [(327, 92, 27, .8), (248, 145, -32, .75), (191, 128, 13, .65), (298, 127, -21, .6)])
    return svg(600, 260, defs + '\n' + branches + '\n' + bloom + '\n' + buds)


def bamboo():
    """Fine, curved bamboo leaves on two airy, diagonal ink-wash sprays."""
    rng = random.Random(20261009)
    width, height = 280, 340
    defs = '''<defs>
 <linearGradient id="blade" x1="0" y1="0" x2=".25" y2="1">
  <stop stop-color="#B7B9A9" stop-opacity=".76"/>
  <stop offset=".38" stop-color="#758575" stop-opacity=".87"/>
  <stop offset=".7" stop-color="#929C88" stop-opacity=".82"/>
  <stop offset="1" stop-color="#CBD0BE" stop-opacity=".62"/>
 </linearGradient>
 <linearGradient id="paleBlade" x1="0" y1="0" x2=".2" y2="1">
  <stop stop-color="#C8C8BA" stop-opacity=".5"/>
  <stop offset=".5" stop-color="#9CA998" stop-opacity=".6"/>
  <stop offset="1" stop-color="#DBD8CE" stop-opacity=".28"/>
 </linearGradient>
 <linearGradient id="stem" x1="0" y1="0" x2="1" y2="0">
  <stop stop-color="#6D7C6C" stop-opacity=".86"/>
  <stop offset=".46" stop-color="#A3AD97" stop-opacity=".8"/>
  <stop offset="1" stop-color="#77866F" stop-opacity=".84"/>
 </linearGradient>
</defs>'''
    stems = '''<g fill="none" stroke="url(#stem)" stroke-linecap="round" stroke-linejoin="round">
 <path stroke-width="3.1" d="M25 346C43 285 63 230 91 180S142 94 199 47"/>
 <path stroke-width="2.1" d="M12 346C31 296 43 250 46 204S41 141 32 112"/>
 <path stroke-width="1.65" d="M47 281C74 256 97 239 126 228S181 213 225 223"/>
 <path stroke-width="1.25" d="M72 216C57 194 52 176 51 153M102 162C121 150 141 145 161 148M134 114C132 96 135 77 143 61M158 85C181 77 199 73 218 75M89 248C96 269 102 285 108 301M163 219C186 232 202 249 212 268M43 250C31 230 24 219 11 211"/>
 <path stroke-width=".7" d="M43 291l-6-2M64 235l-5-2M89 187l-5-2M117 142l-4-2M146 103l-4-2M45 214l-4 0M38 281l-4-1"/>
</g>'''
    # Individual attachment points follow the twigs. Open gaps and drooping
    # blades avoid the repeated fans of the earlier, oversized illustration.
    nodes = [
        (184, 59, -32, 61), (170, 71, -10, 55),
        (198, 74, -14, 55), (210, 75, 16, 48), (177, 79, 40, 52),
        (148, 100, -57, 50), (141, 80, -78, 50), (137, 98, -116, 49),
        (125, 126, -38, 55), (134, 145, -13, 59), (152, 147, 24, 58),
        (140, 146, 61, 51), (114, 150, 91, 51),
        (98, 170, -99, 55), (84, 191, -137, 52),
        (57, 184, -126, 49), (51, 159, -99, 47), (44, 150, -146, 48),
        (40, 131, -91, 49), (47, 206, -158, 46), (43, 227, -125, 51),
        (70, 259, -61, 55), (92, 244, -24, 54), (116, 232, -40, 57),
        (142, 222, -26, 64), (170, 218, -15, 60), (201, 220, 9, 64),
        (221, 222, 37, 52), (187, 236, 58, 61), (201, 253, 75, 59),
        (165, 221, 86, 62), (142, 223, 64, 51), (110, 234, 34, 54),
        (94, 269, 107, 49), (102, 286, 64, 45),
        (40, 269, -154, 47), (31, 294, -83, 46), (47, 282, 40, 49),
    ]
    leaves = []
    for index, (x, y, angle, length) in enumerate(nodes):
        half_width = rng.uniform(2.8, 4.4)
        bend = rng.uniform(-.09, .18) * length
        pale = index % 6 == 3
        fill = 'paleBlade' if pale else 'blade'
        opacity = rng.uniform(.58, .78) if pale else rng.uniform(.78, .98)
        leaves.append(
            f'<g transform="translate({x} {y}) rotate({angle})" opacity="{opacity:.2f}">'
            f'<path fill="url(#{fill})" d="M0 0C{length*.23:.1f} {-half_width:.1f} '
            f'{length*.57:.1f} {bend-half_width*1.8:.1f} {length:.1f} {bend:.1f} '
            f'C{length*.64:.1f} {bend+half_width*.9:.1f} {length*.26:.1f} {half_width:.1f} 0 0Z"/>'
            f'<path fill="none" stroke="#5B705D" stroke-opacity=".44" stroke-width=".45" '
            f'd="M1 0Q{length*.51:.1f} {bend*.36:.1f} {length:.1f} {bend:.1f}"/>'
            f'<path fill="none" stroke="#E5E7D9" stroke-opacity=".45" stroke-width=".5" '
            f'd="M{length*.2:.1f} {-half_width*.36:.1f}Q{length*.47:.1f} '
            f'{bend*.35-half_width*.7:.1f} {length*.72:.1f} {bend*.65-half_width*.3:.1f}"/>'
            '</g>'
        )
    return svg(width, height, defs + '\n' + stems + '\n' + ''.join(leaves))


if __name__ == "__main__":
    for filename, factory in (("paper-texture.svg", paper), ("sakura-branch.svg", sakura), ("bamboo-leaves.svg", bamboo)):
        (ASSETS / filename).write_text(factory(), encoding="utf-8")
        print(filename)
