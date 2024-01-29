import os
from typing import Optional

import matplotlib.pyplot as plt

from ..paths import FIG_PATH


def savefig(
    name: str,
    fig: plt.figure,
    folder: Optional[str] = None,
    format: str = "png",
    dpi: int = 300,
    bbox_inches="tight",
    **kwargs,
) -> None:
    if folder is not None:
        path = FIG_PATH / folder
    else:
        path = FIG_PATH
    if not os.path.exists(path):
        os.makedirs(path)
    savename = name + "." + format
    fig.savefig(
        path / savename, format=format, dpi=dpi, bbox_inches=bbox_inches, **kwargs
    )