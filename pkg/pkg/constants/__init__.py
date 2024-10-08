from pathlib import Path

import seaborn as sns
from caveclient import CAVEclient

RESULTS_PATH = Path(__file__).parent.parent.parent.parent
RESULTS_PATH = RESULTS_PATH / "results"

FIG_PATH = RESULTS_PATH / "figs"

OUT_PATH = RESULTS_PATH / "outs"

DATA_PATH = Path(__file__).parent.parent.parent.parent / "data"

DOC_FIG_PATH = Path(__file__).parent.parent.parent.parent / "docs" / "result_images"

VAR_PATH = Path(__file__).parent.parent.parent.parent / "docs" / "_variables.yml"

# TODO add more of these table names and write them to the quarto yaml
COLUMN_MTYPES_TABLE = "allen_column_mtypes_v2"
MTYPES_TABLE = "aibs_metamodel_mtypes_v661_v2"
NUCLEUS_TABLE = "nucleus_detection_v0"
INHIBITORY_CTYPES_TABLE = "connectivity_groups_v795"
PROOFREADING_TABLE = "proofreading_status_public_release"

MATERIALIZATION_VERSION = 1078

DATASTACK_NAME = "minnie65_phase3_v1"

client = CAVEclient(DATASTACK_NAME)
TIMESTAMP = client.materialize.get_timestamp(MATERIALIZATION_VERSION)

colors = sns.color_palette("Dark2").as_hex()
MERGE_COLOR = colors[0]
SPLIT_COLOR = colors[1]
