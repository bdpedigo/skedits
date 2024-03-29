# %%

import datetime

import caveclient as cc
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from joblib import Parallel, delayed
from networkframe import NetworkFrame

client = cc.CAVEclient("minnie65_phase3_v1")

query_neurons = client.materialize.query_table("connectivity_groups_v795")

root_options = query_neurons["pt_root_id"].values

# %%

# timestamp = pd.to_datetime("2021-07-01 00:00:00", utc=True)
timestamps = pd.date_range("2022-07-01", "2024-01-01", freq="M", tz="UTC")

# %%

object_table = pd.DataFrame()
object_table["working_root_id"] = root_options

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
object_table["current_root_id"] = updated_root_options

# map to nucleus IDs
current_nucs = client.materialize.query_table(
    "nucleus_detection_v0",
    filter_in_dict={"pt_root_id": updated_root_options},
    # select_columns=["id", "pt_root_id"],
).set_index("pt_root_id")["id"]
object_table["target_id"] = object_table["current_root_id"].map(current_nucs)

# %%
timestamp = pd.to_datetime("2021-07-01 00:00:00", utc=True)

nucs = client.materialize.query_table(
    "nucleus_detection_v0",
    filter_in_dict={"id": object_table["target_id"].to_list()},
).set_index("id")
object_table["pt_supervoxel_id"] = object_table["target_id"].map(
    nucs["pt_supervoxel_id"]
)
object_table["timestamp_root_from_chunkedgraph"] = client.chunkedgraph.get_roots(
    object_table["pt_supervoxel_id"], timestamp=timestamp
)

past_nucs = client.materialize.query_table(
    "nucleus_detection_v0",
    filter_in_dict={"id": object_table["target_id"].to_list()},
    # select_columns=["id", "pt_root_id"],
    timestamp=timestamp,
).set_index("id")["pt_root_id"]
object_table["timestamp_root_from_table"] = object_table["target_id"].map(past_nucs)

# %%
import caveclient as cc
from nglui.statebuilder import make_neuron_neuroglancer_link

make_neuron_neuroglancer_link(
    client, 864691135941359220, show_inputs=True, show_outputs=True, timestamp=timestamp
)

# %%
root = 864691135941359220
changelog = client.chunkedgraph.get_tabular_change_log(root)[root]

from pkg.edits import get_detailed_change_log

detailed_changelog = get_detailed_change_log(root, client)


# %%
# for timestamp in timestamps:
def query_for_timestamp(timestamp, timestamp_id, object_table):
    object_table = object_table.copy()
    # for those nucleus IDs, get previous root IDs
    past_nucs = client.materialize.query_table(
        "nucleus_detection_v0",
        filter_in_dict={"id": object_table["target_id"].to_list()},
        # select_columns=["id", "pt_root_id"],
        timestamp=timestamp,
    ).set_index("id")["pt_root_id"]
    object_table["past_root_id"] = object_table["target_id"].map(past_nucs)

    object_table["past_root_id_from_chunkedgraph"] = client.chunkedgraph.get_roots(
        object_table["pt_supervoxel_id"], timestamp=timestamp
    )

    assert (
        object_table["past_root_id"] == object_table["past_root_id_from_chunkedgraph"]
    ).all()

    # for those past root IDs, get the synapses
    client_synapse_table = client.materialize.synapse_query(
        pre_ids=past_nucs,
        post_ids=past_nucs,
        timestamp=timestamp,
        metadata=False,
        remove_autapses=True,
    ).set_index("id")
    client_synapse_table["timestamp"] = timestamp
    client_synapse_table["pre_target_id"] = client_synapse_table["pre_pt_root_id"].map(
        object_table.set_index("past_root_id")["target_id"]
    )
    client_synapse_table["post_target_id"] = client_synapse_table[
        "post_pt_root_id"
    ].map(object_table.set_index("past_root_id")["target_id"])
    client_synapse_table["pre_working_root_id"] = client_synapse_table[
        "pre_pt_root_id"
    ].map(object_table.set_index("past_root_id")["working_root_id"])
    client_synapse_table["post_working_root_id"] = client_synapse_table[
        "post_pt_root_id"
    ].map(object_table.set_index("past_root_id")["working_root_id"])
    return client_synapse_table


synapse_tables_by_time = Parallel(n_jobs=8, verbose=10)(
    delayed(query_for_timestamp)(timestamp, timestamp_id, object_table)
    for timestamp_id, timestamp in enumerate(timestamps)
)

# %%

nodes = object_table.copy().set_index("target_id")
edges = pd.concat(synapse_tables_by_time, axis=0)
edges["source"] = edges["pre_target_id"]
edges["target"] = edges["post_target_id"]
mega_nf = NetworkFrame(nodes, edges)

# %%

# TODO figure out why everything looks the same in this plot; I would expect to see
# many more differences over time for these adjacency matrices

sns.set_context("talk")
fig, axs = plt.subplots(3, 5, figsize=(5 * 5, 5 * 3), constrained_layout=True)
for i in range(0, 30, 2):
    month_nf = mega_nf.query_edges(f"timestamp== @timestamps[{i}]", local_dict=locals())
    adj = month_nf.to_sparse_adjacency(weight_col=None)
    print(adj.sum())
    adj = adj.todense()[:30, :30]
    ax = axs.flat[i // 2]
    sns.heatmap(
        adj,
        ax=ax,
        xticklabels=False,
        yticklabels=False,
        square=True,
        cbar=False,
        cmap="RdBu_r",
        center=0,
        # transform="simple-all",
    )
    ax.set_title(timestamps[i].strftime("%Y-%m"))

# %%
diffs_by_time = pd.DataFrame(index=timestamps, columns=timestamps, dtype=float)
for i in range(0, 30):
    adj1 = mega_nf.query_edges(
        f"timestamp== @timestamps[{i}]", local_dict=locals()
    ).to_sparse_adjacency(weight_col=None)
    adj1 = adj1.todense()
    for j in range(i + 1, 30):
        adj2 = mega_nf.query_edges(
            f"timestamp== @timestamps[{j}]", local_dict=locals()
        ).to_sparse_adjacency(weight_col=None)
        adj2 = adj2.todense()
        diff = np.linalg.norm(adj1 - adj2, ord="fro")
        diffs_by_time.loc[timestamps[i], timestamps[j]] = diff
        diffs_by_time.loc[timestamps[j], timestamps[i]] = diff

diffs_by_time.fillna(0.0, inplace=True)

# %%

fig, ax = plt.subplots(1, 1, figsize=(10, 10))
sns.heatmap(
    diffs_by_time, cmap="RdBu_r", center=0, square=True, ax=ax, cbar_kws={"shrink": 0.5}
)
labels = diffs_by_time.columns.strftime("%Y-%m")
ax.set_yticklabels(labels, rotation=0)
ax.set_xticklabels([])
ax.set_xticks([])

# %%
client_synapse_table.index.difference(synapselist.index)

# %%
times = sequence.sequence_info["datetime"].copy()
times.fillna(pd.to_datetime("2019-07-01 00:00:00", utc=True), inplace=True)
times = times.sort_values()
found_iloc = times.searchsorted(timestamp, side="left") - 1
found_operation = sequence.sequence_info.index[found_iloc]

sequence.sequence_info.loc[found_operation, "timestamp"]

# synapse_sets["time"] = synapse_sets["time"].fillna("2019-07-01 00:00:00")
# synapse_sets["datetime"] = pd.to_datetime(synapse_sets["time"], utc=True)

# %%
edits_to_apply = times.loc[:found_operation].index.dropna()

# %%
test_neuron = (
    neuron.set_edits(edits_to_apply, inplace=False)
    .select_nucleus_component(inplace=False)
    .remove_unused_synapses(inplace=False)
)

# %%
old_root = neuron.edits.loc[242946, "after_root_ids"][0]

# %%

old_l2_graph = client.chunkedgraph.level2_chunk_graph(old_root)

# %%
old_post_synapses = client.materialize.synapse_query(
    post_ids=old_root, timestamp=timestamp
)

# %%
old_pre_synapses
# %%
client.materialize.query_table(
    "synapses_pni_2", filter_equal_dict={"p_pt_root_id": old_root}, timestamp=timestamp
)

# %%

# find the nucleus supervoxel ID that anchors each of these
relevant_nuc_table = nuc_table.set_index("pt_root_id").loc[updated_root_options]

# now for the timestamp I am interested in, get the root IDs of the nucleus at that time
old_root_ids = client.chunkedgraph.get_roots(
    relevant_nuc_table["pt_supervoxel_id"], timestamp=timestamp
)
relevant_nuc_table["old_root_id"] = old_root_ids

# for those root IDs, do a synapse lookup
# TODO use synapse query here
# synapse table has a lot of false positives at nuclei
#
old_synapse_table = client.materialize.query_table(
    "synapses_pni_2",
    filter_in_dict={"pre_pt_root_id": old_root_ids, "post_pt_root_id": old_root_ids},
    timestamp=timestamp,
)

# remove loops
old_synapse_table = old_synapse_table.query("pre_pt_root_id != post_pt_root_id")

# formatting
old_synapse_table = old_synapse_table.set_index("id")
old_synapse_table.index.name = "synapse_id"

# %%
synapselist

# %%

old_synapse_table["pre_pt_root_id"].isin(old_root_ids).all()

# %%
old_synapse_table["post_pt_root_id"].isin(old_root_ids).all()

# %%
# all of my synapses are present in the "correct" synapse table
mask = synapselist.index.isin(old_synapse_table.index)
mask.mean()

# %%

# .99 of these are even in the induced synapses table
mask = old_synapse_table.index.isin(synapselist.index)
print(mask.mean())

old_synapse_table[~mask]

# %%
old_synapse_table.index.isin(induced_synapses).mean()

# %%
old_synapse_table[~old_synapse_table.index.isin(induced_synapses)]

# %%
# 1/3 of synapses from the true version are not even in the intersection of pre and post
# synapse IDs across all time for this collection of neurons

query = root_options[0]
new_query = relevant_nuc_table.loc[query, "old_root_id"]
query_pre_synapses = old_synapse_table.query("pre_pt_root_id == @new_query")

# %%
from pkg.morphology.synapses import get_alltime_synapses

for root_id in root_options[:1]:
    pre_syns, post_syns = get_alltime_synapses(root_id, client)

# %%
query_pre_synapses["id"].isin(pre_syns.index).mean()

# %%


timestamp = datetime.datetime.now(datetime.timezone.utc)

root_id = 864691134886015738
original_roots = client.chunkedgraph.get_original_roots(root_id)
latest_roots = client.chunkedgraph.get_latest_roots(original_roots)
# timestamp = pd.to_datetime("2021-07-01 00:00:00")


side = "pre"
synapse_table = client.info.get_datastack_info()["synapse_table"]
candidate_synapses = client.materialize.query_table(
    synapse_table,
    filter_in_dict={f"{side}_pt_root_id": latest_roots},
)

# %%
# everything in the correct answer ends up in this candidate pool
query_pre_synapses["id"].isin(candidate_synapses["id"]).mean()

# %%
query_pre_synapses["id"].isin(neuron.pre_synapses.index).mean()

# %%
query_pre_synapses.query("pre_pt_root_id != post_pt_root_id")
