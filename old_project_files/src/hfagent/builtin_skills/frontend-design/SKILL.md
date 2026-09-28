---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces. Use whenever building or restyling websites, landing pages, React/HTML/CSS UIs, or when the user says design is poor / looks generic.
license: Complete terms in LICENSE.txt
---

Create distinctive, production-grade interfaces. Working code only — no mockups in chat.

## Before coding

Pick **one** bold direction (e.g. nocturnal workshop, coastal utilitarian, editorial sport, ink+acid) and stick to it. State it in a short CSS comment at the top of the stylesheet.

## Hard layout rules

1. **One composition** in the first viewport — not a dashboard of widgets.
2. **Brand / name first** — hero-level signal, not a tiny nav label. Headline must not overpower the brand.
3. **Hero budget:** brand, one headline, one short supporting sentence, one CTA group, optional full-bleed visual. No stats, schedules, addresses, or promo chips in the first viewport.
4. **Full-bleed hero** on marketing/portfolio surfaces — edge-to-edge atmosphere, not an inset rounded media card.
5. **No overlays** on hero media (floating badges, stickers, pills).
6. **Default: no cards.** Prefer typographic lists, separators, or full-bleed sections. Cards only when they wrap a real interaction.
7. **One job per section** — one purpose, one heading, usually one short line of support.

## Visual rules

- **Typography:** load expressive fonts (e.g. Syne, Fraunces, Instrument Sans, IBM Plex — vary). Never ship Inter / Roboto / Arial / `system-ui` as the only stack.
- **Color:** CSS variables; dominant + sharp accent. Atmosphere via gradient, grain, grid, or photo — not a flat `#fff` / single cream fill.
- **Motion:** at least 2–3 intentional CSS motions (e.g. hero fade-up, link underline grow, subtle background drift). No noisy endless bounce.
- **Anchor:** imagery or strong graphic plane that belongs to the product/place — not decorative purple haze alone.

## Banned AI-default looks

- Purple-on-white or purple→indigo glow themes
- Warm cream (~`#F4F1EA` / `#faf9f5`) + terracotta (`#d97757`) + generic serif display
- Broadsheet / dense newspaper columns with hairline rules
- Defaulting to dark mode “just because”
- Rounded-full pill clusters, multi-layer shadows, emoji as decoration

## Delivery

Rewrite full CSS (and component markup) with `write_file`. Wire fonts in `index.html` or equivalent. Then run tests + curl smoke check. If the UI still looks like a boilerplate CRA page, it is not done — restyle again.
