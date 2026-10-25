def render_finance_bundle(bundle, *, width=100, max_lines=200):
    width = max(20, min(int(width), 240))
    lines = []
    for k, v in sorted(dict(bundle).items()):
        lines.append((f"{k}: {v}")[:width])
    return "\n".join(lines[:max_lines])
