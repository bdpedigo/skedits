# %%

import time

import caveclient as cc
import numpy as np
import pandas as pd
from cloudfiles import CloudFiles
from joblib import Parallel, delayed
from networkframe import NetworkFrame

from pkg.neuronframe import load_neuronframe
from pkg.plot import set_context
from pkg.sequence import create_time_ordered_sequence

# %%

set_context("paper", font_scale=1.5)

client = cc.CAVEclient("minnie65_phase3_v1")

query_neurons = client.materialize.query_table("connectivity_groups_v795")

root_options = query_neurons["pt_root_id"].values


# %%

nodes = pd.DataFrame()
nodes["working_root_id"] = root_options

# take my list of root IDs
# make sure I have the latest root ID for each, using `get_latest_roots`
is_current_mask = client.chunkedgraph.is_latest_roots(root_options)
outdated_roots = root_options[~is_current_mask]
root_map = dict(zip(root_options[is_current_mask], root_options[is_current_mask]))
for outdated_root in outdated_roots:
    latest_roots = client.chunkedgraph.get_latest_roots(outdated_root)
    sub_nucs = client.materialize.query_table(
        "nucleus_detection_v0", filter_in_dict={"pt_root_id": latest_roots}
    )
    if len(sub_nucs) == 1:
        root_map[outdated_root] = sub_nucs.iloc[0]["pt_root_id"]
    else:
        print(f"Multiple nuc roots for {outdated_root}")

updated_root_options = np.array([root_map[root] for root in root_options])
nodes["current_root_id"] = updated_root_options

# map to nucleus IDs
current_nucs = client.materialize.query_table(
    "nucleus_detection_v0",
    filter_in_dict={"pt_root_id": updated_root_options},
    # select_columns=["id", "pt_root_id"],
).set_index("pt_root_id")["id"]
nodes["target_id"] = nodes["current_root_id"].map(current_nucs)


# %%
timestamp = pd.to_datetime("2021-07-01 00:00:00", utc=True)

nucs = client.materialize.query_table(
    "nucleus_detection_v0",
    filter_in_dict={"id": nodes["target_id"].to_list()},
).set_index("id")
nodes["pt_supervoxel_id"] = nodes["target_id"].map(nucs["pt_supervoxel_id"])
nodes["timestamp_root_from_chunkedgraph"] = client.chunkedgraph.get_roots(
    nodes["pt_supervoxel_id"], timestamp=timestamp
)
nodes["nuc_depth"] = nodes["target_id"].map(nucs["pt_position"].apply(lambda x: x[1]))

past_nucs = client.materialize.query_table(
    "nucleus_detection_v0",
    filter_in_dict={"id": nodes["target_id"].to_list()},
    # select_columns=["id", "pt_root_id"],
    timestamp=timestamp,
).set_index("id")["pt_root_id"]
nodes["timestamp_root_from_table"] = nodes["target_id"].map(past_nucs)

mtypes = client.materialize.query_table(
    "allen_column_mtypes_v2", filter_in_dict={"target_id": nodes["target_id"].to_list()}
)
nodes["mtype"] = nodes["target_id"].map(mtypes.set_index("target_id")["cell_type"])

# %%
cloud_bucket = "allen-minnie-phase3"
folder = "edit_sequences"

cf = CloudFiles(f"gs://{cloud_bucket}/{folder}")

files = list(cf.list())
files = pd.DataFrame(files, columns=["file"])

# pattern is root_id=number as the beginning of the file name
# extract the number from the file name and store it in a new column
files["root_id"] = files["file"].str.split("=").str[1].str.split("-").str[0].astype(int)
files["order_by"] = files["file"].str.split("=").str[2].str.split("-").str[0]
files["random_seed"] = files["file"].str.split("=").str[3].str.split("-").str[0]


file_counts = files.groupby("root_id").size()
has_all = file_counts[file_counts == 12].index

files_finished = files.query("root_id in @has_all")

root_options = files_finished["root_id"].unique()

# %%
nodes["has_sequence"] = nodes["current_root_id"].isin(root_options)
nodes["ctype"] = nodes["target_id"].map(
    query_neurons.set_index("target_id")["cell_type"]
)
# %%
root_options = nodes.query("has_sequence")["working_root_id"]

# %%
# timestamps = pd.date_range("2022-07-01", "2024-01-01", freq="M", tz="UTC")


# loop over the entire set of nodes to consider and get the collection of pre
# and post synapses for each, across time


def get_pre_post_synapse_ids(root_id):
    neuron = load_neuronframe(root_id, client, cache_verbose=False)
    return neuron.pre_synapses.index, neuron.post_synapses.index


currtime = time.time()
pre_posts = Parallel(n_jobs=8, verbose=10)(
    delayed(get_pre_post_synapse_ids)(root_id) for root_id in root_options
)
print(f"{time.time() - currtime:.3f} seconds elapsed.")

all_pre_synapses = []
all_post_synapses = []
for pre, post in pre_posts:
    all_pre_synapses.extend(pre)
    all_post_synapses.extend(post)

# %%

# the induced synapses is the set of all synapses which show up at least once pre, and
# at least once post across all time

induced_synapses = np.intersect1d(all_pre_synapses, all_post_synapses)

print("Number of induced synapses:", len(induced_synapses))

# %%

# now, extract the synapses that are in this induced set for each neuron.


def process_resolved_synapses(sequence, root_id, which="pre"):
    synapse_sets = sequence.sequence_info[f"{which}_synapses"]
    synapse_sets = synapse_sets.apply(
        lambda x: np.intersect1d(x, induced_synapses)
    ).to_frame()
    synapse_sets["time"] = sequence.edits["time"]
    synapse_sets["time"] = synapse_sets["time"].fillna("2019-07-01 00:00:00")
    synapse_sets["datetime"] = pd.to_datetime(synapse_sets["time"], utc=True)
    synapse_sets[f"{which}_root_id"] = root_id

    breaks = list(synapse_sets["datetime"])
    breaks.append(
        pd.to_datetime("2070-01-01 00:00:00", utc=True)
    )  # TODO could make this explicit about now
    intervals = pd.IntervalIndex.from_breaks(breaks, closed="left")
    synapse_sets["interval"] = intervals

    synapse_sets = synapse_sets.reset_index(drop=False)
    synapses = synapse_sets.explode(f"{which}_synapses").rename(
        columns={f"{which}_synapses": "synapse_id"}
    )
    return synapses


def get_info_by_time(root_id):
    neuron = load_neuronframe(root_id, client, cache_verbose=False)
    sequence = create_time_ordered_sequence(neuron, root_id)
    pre_synapse_sets = process_resolved_synapses(sequence, root_id, which="pre")
    post_synapse_sets = process_resolved_synapses(sequence, root_id, which="post")
    edits = neuron.edits.copy()
    edits["root_id"] = root_id
    return pre_synapse_sets, post_synapse_sets, edits


outs = Parallel(n_jobs=8, verbose=10)(
    delayed(get_info_by_time)(root_id) for root_id in root_options
)


pre_synapselist = []
post_synapselist = []
all_edit_tables = []

for pre, post, edit_table in outs:
    pre_synapselist.append(pre)
    post_synapselist.append(post)
    all_edit_tables.append(edit_table)

pre_synapses = pd.concat(pre_synapselist)
post_synapses = pd.concat(post_synapselist)
all_edits = pd.concat(all_edit_tables)

# %%


# %%
def synapselist_at_time(timestamp, remove_loops=True):
    # pre_synapses_at_time = pre_synapses.query("@timestamp in interval")
    # post_synapses_at_time = post_synapses.query("@timestamp in interval")
    pre_synapses_at_time = pre_synapses[
        pd.IntervalIndex(pre_synapses.interval).contains(timestamp)
    ]
    post_synapses_at_time = post_synapses[
        pd.IntervalIndex(post_synapses.interval).contains(timestamp)
    ]

    pre_synapses_at_time = pre_synapses_at_time.set_index("synapse_id")
    post_synapses_at_time = post_synapses_at_time.set_index("synapse_id")
    synapselist = pre_synapses_at_time.join(
        post_synapses_at_time, how="inner", lsuffix="_pre", rsuffix="_post"
    )
    synapselist["source"] = synapselist["pre_root_id"]
    synapselist["target"] = synapselist["post_root_id"]
    if remove_loops:
        synapselist = synapselist.query("source != target")
    return synapselist


synapselist = synapselist_at_time(pd.to_datetime("2021-07-01 00:00:00", utc=True))

# %%
from sklearn.model_selection import StratifiedShuffleSplit

p_effort = 0.5

sss = StratifiedShuffleSplit(n_splits=10, train_size=0.5, random_state=88)

for sub_ilocs, _ in sss.split(nodes.index, nodes["mtype"]):
    sub_nodes = nodes.iloc[sub_ilocs]
    sub_nodes.groupby("mtype").size()

# %%
root_id = sub_nodes.iloc[0]["working_root_id"]
neuron = load_neuronframe(root_id, client, cache_verbose=False)
sequence = create_time_ordered_sequence(neuron, root_id)
pre_synapse_sets = process_resolved_synapses(sequence, root_id, which="pre")
post_synapse_sets = process_resolved_synapses(sequence, root_id, which="post")
edits = neuron.edits.copy()
edits["root_id"] = root_id

# %%

from tqdm.auto import tqdm

working_nodes = nodes.query("has_sequence")
rows = []
for root_id in tqdm(working_nodes["working_root_id"].unique()[:]):
    neuron = load_neuronframe(root_id, client, cache_verbose=False)
    sequence = create_time_ordered_sequence(neuron, root_id)
    for p_neuron_effort in [0, 0.5, 1.0]:
        n_total_edits = len(sequence) - 1
        n_select_edits = np.floor(n_total_edits * p_neuron_effort).astype(int)
        selected_state = sequence.sequence_info.iloc[n_select_edits]

        row = {
            "root_id": root_id,
            "p_neuron_effort": p_neuron_effort,
            "n_total_edits": n_total_edits,
            "n_select_edits": n_select_edits,
            "order": selected_state["order"],
        }

        for which in ["pre", "post"]:
            selected_synapses = selected_state[f"{which}_synapses"]
            selected_synapses = np.intersect1d(selected_synapses, induced_synapses)
            row[f"{which}_synapses"] = selected_synapses.tolist()
        rows.append(row)

# %%
synapse_selection_df = pd.DataFrame(rows)

# %%
synapse_selection_df.query("p_neuron_effort == 0.5")["n_select_edits"].sum()

# %%
efforts = []
for _ in range(50):
    efforts.append(
        synapse_selection_df.query("p_neuron_effort == 1")
        .sample(frac=0.5)["n_select_edits"]
        .sum()
    )
print(np.mean(efforts))

# %%

p_effort = 0.5

nfs_by_strategy = {}
stats = {}
for p_neuron_effort in [0, 0.5, 1.0]:
    synapse_selections_at_effort = synapse_selection_df.query(
        "p_neuron_effort == @p_neuron_effort"
    )
    for p_neurons in [0.5, 1.0]:
        if p_neurons == 1.0:
            index_list = [(np.arange(len(working_nodes)), None)]
        else:
            sss = StratifiedShuffleSplit(
                n_splits=10, train_size=p_neurons, random_state=88
            )
            index_list = sss.split(working_nodes.index, working_nodes["mtype"])

        for i, (sub_ilocs, _) in enumerate(index_list):
            sub_nodes = working_nodes.iloc[sub_ilocs]
            sub_roots = sub_nodes["working_root_id"]
            sub_synapse_selections = synapse_selections_at_effort.query(
                "root_id in @sub_roots"
            )

            pre_synapses_long = (
                sub_synapse_selections.explode("pre_synapses")[
                    ["root_id", "pre_synapses"]
                ]
                .rename({"root_id": "source"}, axis=1)
                .dropna()
                .set_index("pre_synapses")
            )
            post_synapses_long = (
                sub_synapse_selections.explode("post_synapses")[
                    ["root_id", "post_synapses"]
                ]
                .rename({"root_id": "target"}, axis=1)
                .dropna()
                .set_index("post_synapses")
            )

            sub_edges = pre_synapses_long.join(
                post_synapses_long, how="inner", lsuffix="_pre", rsuffix="_post"
            )
            sub_edges = sub_edges.query("source != target")

            nfs_by_strategy[(p_neurons, p_neuron_effort, i)] = NetworkFrame(
                nodes=sub_nodes.set_index("working_root_id"), edges=sub_edges
            )
            row = {
                "n_edges": len(sub_edges),
                "n_nodes": len(sub_nodes),
                "n_select_edits": sub_synapse_selections["n_select_edits"].sum(),
            }
            stats[(p_neurons, p_neuron_effort, i)] = row

# %%
stats_by_strategy = pd.DataFrame(stats).T
stats_by_strategy.index.set_names(
    ["p_neurons", "p_neuron_effort", "split"], inplace=True
)

# %%
synapse_group_counts_by_strategy = []
for key, nf in nfs_by_strategy.items():
    groupby = nf.groupby_nodes("mtype")
    synapse_counts = groupby.apply_edges("size")
    synapse_counts.name = "n_synapses"
    synapse_counts = synapse_counts.to_frame()
    synapse_counts["p_neurons"] = key[0]
    synapse_counts["p_neuron_effort"] = key[1]
    synapse_counts["split"] = key[2]
    # synapse_counts.name = key
    synapse_group_counts_by_strategy.append(synapse_counts)

synapse_group_counts_by_strategy = pd.concat(synapse_group_counts_by_strategy)
synapse_group_counts_by_strategy

# %%
synapse_group_counts_by_strategy.reset_index(inplace=True)

# %%
synapse_group_counts_by_strategy["connection"] = list(
    zip(
        synapse_group_counts_by_strategy["source_mtype"],
        synapse_group_counts_by_strategy["target_mtype"],
    )
)

# %%
synapse_group_counts_by_strategy[
    "n_select_edits"
] = synapse_group_counts_by_strategy.set_index(
    ["p_neurons", "p_neuron_effort", "split"]
).index.map(stats_by_strategy["n_select_edits"])

# %%
synapse_group_counts_by_strategy["strategy"] = list(
    zip(
        synapse_group_counts_by_strategy["p_neurons"],
        synapse_group_counts_by_strategy["p_neuron_effort"],
    )
)
# %%
synapse_group_counts_by_strategy["p_synapses"] = (
    synapse_group_counts_by_strategy["n_synapses"]
    / synapse_group_counts_by_strategy["p_neurons"]
)

# %%
import matplotlib.pyplot as plt
import seaborn as sns

fig, axs = plt.subplots(
    4, 4, figsize=(10, 10), sharex=True, sharey=False, constrained_layout=True
)
axs = pd.DataFrame(
    axs,
    index=synapse_group_counts_by_strategy["source_mtype"].unique(),
    columns=synapse_group_counts_by_strategy["target_mtype"].unique(),
)
for source_mtype, sub_df in synapse_group_counts_by_strategy.groupby("source_mtype"):
    for target_mtype, sub_sub_df in sub_df.groupby("target_mtype"):
        ax = axs.loc[source_mtype, target_mtype]
        sns.scatterplot(
            data=sub_sub_df,
            x="n_select_edits",
            y="p_synapses",
            hue="strategy",
            ax=ax,
            legend=False,
        )

# Need to generalize this better for arbitrary metrics on the subgraph, or something like that
# redo this but using the average number of synapses from a group k neuron to a groul l neuron
# as the metric of interest

# %%
