"""User framing, independent of optional/random output transformations."""
import math


def source_zoom(config, is_hook):
    key = 'hook_zoom_percent' if is_hook else 'body_zoom_percent'
    try:
        percent = float(config.get(key, 100))
    except (TypeError, ValueError):
        raise ValueError(f'{key}: 放大比例必须为 100–300%') from None
    if not math.isfinite(percent) or not 100 <= percent <= 300:
        raise ValueError(f'{key}: 放大比例必须为 100–300%')
    return percent / 100


def source_crop_filter(config, is_hook):
    zoom = source_zoom(config, is_hook)
    if zoom == 1:
        return ''  # Preserve the original filter chain and cost at defaults.
    factor = f'{zoom:.9f}'
    # Crop before the existing scale, avoiding a large upscaled intermediate.
    return (f'crop=w=max(2\\,trunc(iw/{factor}/2)*2):'
            f'h=max(2\\,trunc(ih/{factor}/2)*2):x=(iw-ow)/2:y=(ih-oh)/2,')
