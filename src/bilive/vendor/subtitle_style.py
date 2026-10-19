from __future__ import annotations
from dataclasses import dataclass

def validate_style(value):
    import math, re
    allowed = {'font_name','font_size','margin_v','alignment','outline','primary_colour','outline_colour',
               'bold','spacing','shadow','shadow_colour','shadow_outline'}
    if set(value) - allowed:
        raise ValueError('unknown subtitle style field')
    font = value.get('font_name', 'Noto Sans SC')
    if not isinstance(font, str) or not font.strip() or len(font) > 100 or any(c in font for c in ',\r\n\\{}'):
        raise ValueError('invalid subtitle font name')
    if 'bold' in value and type(value['bold']) is not bool:
        raise ValueError('invalid subtitle bold flag')
    for key, low, high in [('font_size',8,120),('margin_v',0,1080),('alignment',1,9),('outline',0,10),
                           ('spacing',0,30),('shadow',0,30),('shadow_outline',0,30)]:
        if key in value:
            try:
                number = float(value[key])
            except (TypeError, ValueError):
                raise ValueError('invalid subtitle style number') from None
            if not math.isfinite(number) or not low <= number <= high or (key in {'font_size','margin_v','alignment'} and number != int(number)):
                raise ValueError('invalid subtitle style number')
    for key in ('primary_colour','outline_colour','shadow_colour'):
        if key in value and not re.fullmatch(r'&H[0-9A-Fa-f]{8}', str(value[key])):
            raise ValueError('invalid ASS colour')

def _format_style_number(value: float) -> str:
    """Render a style number without trailing zeros (2.0 -> '2', 1.5 -> '1.5')."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True)
class SubtitleStyle:
    """Configurable burned-subtitle appearance.

    ``font_name`` is explicit because the browser preview and the final
    libass/ffmpeg render must use the same intended font. Optional placement,
    colour, and outline fields are omitted from the filter when unset.
    """

    font_name: str = "Noto Sans SC"
    font_size: int = 20
    margin_v: int = 60
    alignment: int | None = None      # ASS numpad alignment (1-9)
    outline: float | None = None      # outline (描边) width
    primary_colour: str | None = None  # ASS &HAABBGGRR
    outline_colour: str | None = None  # ASS &HAABBGGRR
    bold: bool | None = None
    spacing: float | None = None
    shadow: float | None = None       # diagonal shadow distance in pixels
    shadow_colour: str | None = None
    shadow_outline: float | None = None  # expanded shadow, rendered on its own layer

    def to_force_style(self) -> str:
        parts = [
            f"FontName={self.font_name}",
            f"Fontsize={int(self.font_size)}",
            f"MarginV={int(self.margin_v)}",
        ]
        if self.alignment is not None:
            parts.append(f"Alignment={int(self.alignment)}")
        if self.outline is not None:
            parts.append(f"Outline={_format_style_number(self.outline)}")
        if self.primary_colour:
            parts.append(f"PrimaryColour={self.primary_colour}")
        if self.outline_colour:
            parts.append(f"OutlineColour={self.outline_colour}")
        if self.bold is not None:
            parts.append(f"Bold={-1 if self.bold else 0}")
        if self.spacing is not None:
            parts.append(f"Spacing={_format_style_number(self.spacing)}")
        if self.shadow is not None:
            parts.append(f"Shadow={_format_style_number(self.shadow / 2 ** .5)}")
        if self.shadow_colour:
            parts.append(f"BackColour={self.shadow_colour}")
        return ",".join(parts)

    @classmethod
    def from_mapping(cls, data: dict | None) -> "SubtitleStyle":
        """Build a style from a plain mapping, ignoring unknown/blank fields."""
        base = cls()
        if not isinstance(data, dict):
            return base

        def _int(key, default):
            value = data.get(key)
            try:
                return int(value) if value is not None and value != "" else default
            except (TypeError, ValueError):
                return default

        def _opt_int(key):
            value = data.get(key)
            try:
                return int(value) if value is not None and value != "" else None
            except (TypeError, ValueError):
                return None

        def _opt_float(key):
            value = data.get(key)
            try:
                return float(value) if value is not None and value != "" else None
            except (TypeError, ValueError):
                return None

        def _opt_str(key):
            value = data.get(key)
            text = str(value).strip() if value is not None else ""
            return text or None

        return cls(
            font_name=_opt_str("font_name") or base.font_name,
            font_size=_int("font_size", base.font_size),
            margin_v=_int("margin_v", base.margin_v),
            alignment=_opt_int("alignment"),
            outline=_opt_float("outline"),
            primary_colour=_opt_str("primary_colour"),
            outline_colour=_opt_str("outline_colour"),
            bold=data.get("bold") if type(data.get("bold")) is bool else None,
            spacing=_opt_float("spacing"),
            shadow=_opt_float("shadow"),
            shadow_colour=_opt_str("shadow_colour"),
            shadow_outline=_opt_float("shadow_outline"),
        )

    def to_mapping(self) -> dict:
        """Serialise non-default fields for persistence in task metadata."""
        data: dict = {
            "font_name": self.font_name,
            "font_size": int(self.font_size),
            "margin_v": int(self.margin_v),
        }
        if self.alignment is not None:
            data["alignment"] = int(self.alignment)
        if self.outline is not None:
            data["outline"] = float(self.outline)
        if self.primary_colour:
            data["primary_colour"] = self.primary_colour
        if self.outline_colour:
            data["outline_colour"] = self.outline_colour
        for key in ('bold', 'spacing', 'shadow', 'shadow_colour', 'shadow_outline'):
            if getattr(self, key) is not None:
                data[key] = getattr(self, key)
        return data
