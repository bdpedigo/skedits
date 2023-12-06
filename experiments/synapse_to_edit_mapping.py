# %%
import os
import pickle

import caveclient as cc
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import seaborn as sns
import seaborn.objects as so
from neuropull.graph import NetworkFrame
from sklearn.metrics import pairwise_distances_argmin
from tqdm.autonotebook import tqdm

from pkg.edits import (
    collate_edit_info,
    find_supervoxel_component,
    get_operation_metaoperation_map,
    lazy_load_network_edits,
    reverse_edit,
)
from pkg.neuroglancer import (
    add_level2_edits,
    finalize_link,
    generate_neuron_base_builders,
)
from pkg.plot import radial_hierarchy_pos
from pkg.utils import get_level2_nodes_edges, get_nucleus_point_nm

# %%

client = cc.CAVEclient("minnie65_phase3_v1")

query_neurons = client.materialize.query_table("connectivity_groups_v507")
query_neurons.sort_values("id", inplace=True)

nuc = client.materialize.query_table(
    "nucleus_detection_v0",  # select_columns=["pt_supervoxel_id", "pt_root_id"]
).set_index("pt_root_id")

# %%

os.environ["SKEDITS_USE_CLOUD"] = "True"
os.environ["SKEDITS_RECOMPUTE"] = "False"

# %%

# getting a table of additional metadata for each operation
# TODO something weird with 3, 6

root_id = query_neurons["pt_root_id"].values[13]

(
    networkdeltas_by_operation,
    networkdeltas_by_metaoperation,
) = lazy_load_network_edits(root_id, client=client)

# %%

operation_to_metaoperation = get_operation_metaoperation_map(
    networkdeltas_by_metaoperation
)

# %%

edit_stats, modified_level2_nodes = collate_edit_info(
    networkdeltas_by_operation, operation_to_metaoperation, root_id, client
)

# %%

so.Plot(
    edit_stats.query("is_merge & is_filtered"),
    x="n_modified_nodes",
    y="centroid_distance_to_nuc_um",
).add(so.Dots(pointsize=8, alpha=0.9))

# %%

# because for this analysis we are just interested in rolling back merges from the
# most proofread version of this neuron, we can safely just use the synapses from the
# latest version

# TODO this needs to be redone with the query for all synapses ever potentially on a
# neuron, not just the latest version

pre_synapses = client.materialize.query_table(
    "synapses_pni_2", filter_equal_dict={"pre_pt_root_id": root_id}
)
pre_synapses.query("pre_pt_root_id != post_pt_root_id", inplace=True)
pre_synapses.set_index("id", inplace=True)
pre_synapses["pre_pt_current_level2_id"] = client.chunkedgraph.get_roots(
    pre_synapses["pre_pt_supervoxel_id"], stop_layer=2
)

post_synapses = client.materialize.query_table(
    "synapses_pni_2", filter_equal_dict={"post_pt_root_id": root_id}
)
post_synapses.query("pre_pt_root_id != post_pt_root_id", inplace=True)
post_synapses.set_index("id", inplace=True)
post_synapses["post_pt_current_level2_id"] = client.chunkedgraph.get_roots(
    post_synapses["post_pt_supervoxel_id"], stop_layer=2
)

# %%

from pkg.edits import get_initial_network

# get the initial network
initial_nf = get_initial_network(root_id, client, positions=True)
# %%

# create a networkframe for the final network and add annotations for synapses
nodes, edges = get_level2_nodes_edges(root_id, client, positions=True)
final_nf = NetworkFrame(nodes, edges)
# %%

from pkg.edits import apply_additions

final_nf = initial_nf.copy()

# %%
for networkdelta in tqdm(networkdeltas_by_operation.values()):
    apply_additions(final_nf, networkdelta)

# %%
# apply positions to ALL nodes

from pkg.utils import get_positions

node_positions = get_positions(list(final_nf.nodes.index), client)

# %%

final_nf.nodes["synapses"] = [[] for _ in range(len(final_nf.nodes))]
final_nf.nodes["pre_synapses"] = [[] for _ in range(len(final_nf.nodes))]
final_nf.nodes["post_synapses"] = [[] for _ in range(len(final_nf.nodes))]

for idx, synapse in pre_synapses.iterrows():
    final_nf.nodes.loc[synapse["pre_pt_current_level2_id"], "synapses"].append(idx)
    final_nf.nodes.loc[synapse["pre_pt_current_level2_id"], "pre_synapses"].append(idx)

for idx, synapse in post_synapses.iterrows():
    final_nf.nodes.loc[synapse["post_pt_current_level2_id"], "synapses"].append(idx)
    final_nf.nodes.loc[synapse["post_pt_current_level2_id"], "post_synapses"].append(
        idx
    )

# annotate a point on the level2 graph as the nucleus; whatever is closest
nuc_pt_nm = get_nucleus_point_nm(root_id, client, method="table")

pos_nodes = final_nf.nodes[["x", "y", "z"]]
pos_nodes = pos_nodes[pos_nodes.notna().all(axis=1)]

ind = pairwise_distances_argmin(nuc_pt_nm.reshape(1, -1), pos_nodes)[0]
nuc_level2_id = pos_nodes.index[ind]
final_nf.nodes["nucleus"] = False
final_nf.nodes.loc[nuc_level2_id, "nucleus"] = True

# add annotations for metaoperations to the networkframe
final_nf.nodes["metaoperation_id"] = np.nan
final_nf.nodes["metaoperation_id"] = final_nf.nodes["metaoperation_id"].astype("Int64")

for metaoperation_id, networkdelta in networkdeltas_by_metaoperation.items():
    added_nodes = networkdelta.added_nodes
    # TODO more robustly deal with add->removes in the metaedits
    # TODO maybe just take the intersection with the final network as a shortcut
    #      Since some of the metaedits might not have merges which are relevant so added
    #      nodes get removed later or something...
    net_added_node_ids = networkdelta.added_nodes.index.difference(
        networkdelta.removed_nodes.index
    )

    net_added_node_ids = networkdelta.added_nodes.index.intersection(
        final_nf.nodes.index
    )

    if len(net_added_node_ids) == 0:
        print(networkdelta.metadata["is_merges"])
        for operation_id in networkdelta.metadata["operation_ids"]:
            print(edit_stats.loc[operation_id, "is_filtered"])
        print()

    if not final_nf.nodes.loc[net_added_node_ids, "metaoperation_id"].isna().all():
        raise AssertionError("Some nodes already exist")
    else:
        final_nf.nodes.loc[net_added_node_ids, "metaoperation_id"] = metaoperation_id


# %%

# plot this neuron with its merge edits, grouped by metaoperation

state_builders, dataframes = generate_neuron_base_builders(root_id, client)

edits_to_show = edit_stats.query("is_merge & is_filtered")

level2_nodes_to_show = modified_level2_nodes.query(
    "is_merge & is_filtered"
).reset_index()

state_builders, dataframes = add_level2_edits(
    state_builders, dataframes, level2_nodes_to_show, final_nf, client
)

link = finalize_link(state_builders, dataframes, client)
link


# %%

nuc_dist_threshold = 10
n_modified_nodes_threshold = 10
soma_nuc_merge_metaoperation = None

candidate_metaedits = []
for metaoperation_id, networkdelta in networkdeltas_by_metaoperation.items():
    metadata = networkdelta.metadata
    operation_ids = metadata["operation_ids"]

    has_filtered = False
    has_soma_nuc = False
    for operation_id in operation_ids:
        # check if any of the operations in this metaoperation are filtered
        is_filtered = edit_stats.loc[operation_id, "is_filtered"]
        if is_filtered:
            has_filtered = True

        # check if any of the operations in this metaoperation are a soma/nucleus merge
        is_merge = edit_stats.loc[operation_id, "is_merge"]
        if is_merge:
            dist_um = edit_stats.loc[operation_id, "centroid_distance_to_nuc_um"]
            n_modified_nodes = edit_stats.loc[operation_id, "n_modified_nodes"]
            if (
                dist_um < nuc_dist_threshold
                and n_modified_nodes >= n_modified_nodes_threshold
            ):
                has_soma_nuc = True
                soma_nuc_merge_metaoperation = metaoperation_id
                print("Found soma/nucleus merge metaoperation: ", metaoperation_id)
                break

    if has_filtered and metadata["any_merges"] and (not has_soma_nuc):
        candidate_metaedits.append(metaoperation_id)

candidate_metaedits = pd.Series(candidate_metaedits)

# %%
adjacency = final_nf.to_sparse_adjacency()

final_nf.nodes["iloc"] = np.arange(len(final_nf.nodes))

metaoperations_to_query = edit_stats.query("is_filtered & is_merge")[
    "metaoperation_id"
].unique()
# metaoperations_to_query = list(edit_df["metaoperation_id"].unique())
final_nf_keypoints = final_nf.nodes.query(
    "metaoperation_id.isin(@metaoperations_to_query) | nucleus", engine="python"
)

# %%

# add distance information to edges
final_nf = final_nf.apply_node_features(["x", "y", "z"], axis="both")
final_nf.edges["distance_nm"] = np.sqrt(
    (final_nf.edges["source_x"] - final_nf.edges["target_x"]) ** 2
    + (final_nf.edges["source_y"] - final_nf.edges["target_z"]) ** 2
    + (final_nf.edges["source_z"] - final_nf.edges["target_z"]) ** 2
)
final_nf.edges["distance_um"] = final_nf.edges["distance_nm"] / 1000

# %%


g = final_nf.to_networkx().to_undirected()

predecessors_by_metaoperation = {}

for metaoperation_id, metaoperation_points in final_nf_keypoints.groupby(
    "metaoperation_id", dropna=True
):
    if metaoperation_id == soma_nuc_merge_metaoperation:
        continue
    min_path_length = np.inf
    min_path = None
    for level2_id in metaoperation_points.index:
        # TODO rather than choosing the one with shortest path length, might be best to
        # choose the one with minimal dependency set, somehow...
        path = nx.shortest_path(
            g, source=nuc_level2_id, target=level2_id, weight="distance_um"
        )
        path_length = nx.shortest_path_length(
            g, source=nuc_level2_id, target=level2_id, weight="distance_um"
        )
        if path_length < min_path_length:
            min_path_length = path_length
            min_path = path

    min_path = pd.Index(min_path)

    keypoint_predecessors = min_path.intersection(final_nf_keypoints.index)

    metaoperation_predecessors = final_nf_keypoints.loc[
        keypoint_predecessors, "metaoperation_id"
    ]
    metaoperation_predecessors = metaoperation_predecessors[
        ~metaoperation_predecessors.isna()
    ]
    metaoperation_predecessors = metaoperation_predecessors[
        metaoperation_predecessors != metaoperation_id
    ]
    predecessors_by_metaoperation[metaoperation_id] = list(
        metaoperation_predecessors.unique()
    )

# %%
# decent amount of redundant computation here but seems fast enough...

predecessors_by_synapse = {}
path_lengths_by_synapse = {}
for synapse_id, row in tqdm(pre_synapses.iterrows(), total=len(pre_synapses)):
    level2_id = row["pre_pt_current_level2_id"]
    path = nx.shortest_path(
        g, source=nuc_level2_id, target=level2_id, weight="distance_um"
    )
    # path_length = nx.shortest_path_length(
    #     g, source=nuc_level2_id, target=level2_id, weight="distance_um"
    # )

    starts = final_nf.nodes.loc[path[:-1], ["x", "y", "z"]]
    ends = final_nf.nodes.loc[path[1:], ["x", "y", "z"]]
    path_length_nm = np.sqrt(((starts.values - ends.values) ** 2).sum(axis=1)).sum()

    path = pd.Index(path)

    keypoint_predecessors = path.intersection(final_nf_keypoints.index)

    metaoperation_predecessors = final_nf_keypoints.loc[
        keypoint_predecessors, "metaoperation_id"
    ]
    metaoperation_predecessors = metaoperation_predecessors[
        ~metaoperation_predecessors.isna()
    ]
    metaoperation_predecessors = metaoperation_predecessors[
        metaoperation_predecessors != metaoperation_id
    ]
    predecessors_by_synapse[synapse_id] = list(metaoperation_predecessors.unique())
    path_lengths_by_synapse[synapse_id] = path_length_nm

# %%

synapse_metaoperation_dependencies = pd.Series(predecessors_by_synapse)
pre_synapses["metaoperation_dependencies"] = synapse_metaoperation_dependencies
pre_synapses["n_metaoperation_dependencies"] = pre_synapses[
    "metaoperation_dependencies"
].apply(len)

synapse_path_lengths = pd.Series(path_lengths_by_synapse)
pre_synapses["path_length_nm"] = synapse_path_lengths
pre_synapses["path_length_um"] = pre_synapses["path_length_nm"] / 1000


# %%

plot = (
    so.Plot(data=pre_synapses, x="path_length_um", y="n_metaoperation_dependencies")
    .add(so.Dots(alpha=0.1, pointsize=3), so.Jitter(x=0, y=0.4))
    .label(
        title=f"Root ID {root_id}, Found soma/nuc merge = {soma_nuc_merge_metaoperation is not None}",
        x="Path length (um)",
        y="# of metaoperation dependencies",
    )
)
plot.save(f"path_length_vs_n_dependencies-root={root_id}")

# %%
plot = (
    so.Plot(data=pre_synapses, x="n_metaoperation_dependencies")
    .add(so.Bar(width=1), so.Hist(discrete=True, stat="proportion"))
    .label(
        x="# of metaoperation dependencies",
        y="Proportion of synapses",
        title=f"Root ID {root_id}, Found soma/nuc merge = {soma_nuc_merge_metaoperation is not None}",
    )
)
plot.save(f"n_dependencies_hist-root={root_id}")

# %%

metaoperation_synapse_dependents = pre_synapses.explode("metaoperation_dependencies")[
    "metaoperation_dependencies"
].value_counts()
metaoperation_synapse_dependents = metaoperation_synapse_dependents.reindex(
    metaoperations_to_query
).fillna(0)


# %%


state_builders, dataframes = generate_neuron_base_builders(root_id, client)

edits_to_show = edit_stats.query("is_merge & is_filtered")

level2_nodes_to_show = modified_level2_nodes.query(
    "is_merge & is_filtered"
).reset_index()

state_builders, dataframes = add_level2_edits(
    state_builders, dataframes, level2_nodes_to_show, final_nf, client
)

# for path, pathnames in zip([path_489, path_485, path_484], ["489", "485", "484"]):
#     level2_graph_mapper = PointMapper(
#         point_column="rep_coord_nm",
#         description_column="l2_id",
#         split_positions=False,
#         gather_linked_segmentations=False,
#         set_position=False,
#         collapse_groups=True,
#     )
#     level2_graph_layer = AnnotationLayerConfig(
#         f"path-{pathnames}",
#         data_resolution=[1, 1, 1],
#         color=(0.6, 0.6, 0.6),
#         mapping_rules=level2_graph_mapper,
#     )
#     level2_graph_statebuilder = StateBuilder(
#         layers=[level2_graph_layer],
#         client=client,  # view_kws=view_kws
#     )
#     state_builders.append(level2_graph_statebuilder)
#     path = pd.Index(path, name="l2_id")
#     dataframes.append(final_nf.nodes.loc[path].reset_index())


link = finalize_link(state_builders, dataframes, client)
link


# %%
metaoperation_dependencies = nx.DiGraph()

for metaoperation, predecessors in predecessors_by_metaoperation.items():
    path = predecessors + [metaoperation]
    if soma_nuc_merge_metaoperation is None:
        path = [-1] + path
    nx.add_path(metaoperation_dependencies, path)

is_tree = nx.is_tree(metaoperation_dependencies)

print("Merge dependency graph is a tree: ", is_tree)


# %%

nontrivial_metaoperation_dependencies = metaoperation_dependencies.copy()
out_degrees = metaoperation_dependencies.out_degree()

in_degrees = list(metaoperation_dependencies.in_degree())
in_degrees = list(zip(*in_degrees))
in_degrees = pd.Series(index=in_degrees[0], data=in_degrees[1])
max_degree_node = in_degrees.idxmax()

# %%
for node_id in metaoperation_dependencies.nodes():
    in_edges = metaoperation_dependencies.in_edges(node_id)
    n_edges = len(in_edges)
    if n_edges == 0:
        continue
    first_edge = next(iter(in_edges))
    if n_edges == 1 and first_edge == (max_degree_node, node_id):
        nontrivial_metaoperation_dependencies.remove_node(node_id)

# %%


fig, ax = plt.subplots(1, 1, figsize=(15, 15))
pos = radial_hierarchy_pos(metaoperation_dependencies)

nodelist = metaoperation_synapse_dependents.index.to_list()
if soma_nuc_merge_metaoperation is None:
    nodelist = [-1] + nodelist

node_sizes = list(metaoperation_synapse_dependents.values)
if soma_nuc_merge_metaoperation is None:
    node_sizes = [max(node_sizes) + 1] + node_sizes

nx.draw_networkx(
    metaoperation_dependencies,
    nodelist=nodelist,
    pos=pos,
    with_labels=True,
    ax=ax,
    node_size=node_sizes,
    font_size=6,
)
ax.axis("equal")
ax.axis("off")
plt.savefig(f"merge_dependency_tree_root={root_id}.png", bbox_inches="tight", dpi=300)


# %%

nuc_supervoxel = nuc.loc[root_id, "pt_supervoxel_id"]
p_merge_rollbacks = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
n_samples = 25

nfs_by_sample = {}
for p_merge_rollback in p_merge_rollbacks:
    if p_merge_rollback == 0.0 or p_merge_rollback == 1.0:
        _n_samples = 1
    else:
        _n_samples = n_samples
    for i in tqdm(range(_n_samples)):
        sampled_metaedits = candidate_metaedits.sample(frac=p_merge_rollback)

        nf = final_nf.copy()
        for metaedit in sampled_metaedits:
            networkdelta = networkdeltas_by_metaoperation[metaedit]
            reverse_edit(nf, networkdelta)

        nuc_nf = find_supervoxel_component(nuc_supervoxel, nf, client)
        if p_merge_rollback == 0.0:
            assert nuc_nf == final_nf
        nfs_by_sample[(np.round(1 - p_merge_rollback, 1), i)] = nuc_nf

# %%
synapses_by_sample = {}
for key, nf in nfs_by_sample.items():
    all_synapses = []
    for idx, node in nf.nodes.iterrows():
        # TODO I think can safely ignore nodes w/o "synapses" column here since they
        # were added, but could check this
        if isinstance(node["synapses"], list):
            all_synapses += node["synapses"]
    synapses_by_sample[key] = all_synapses

# %%
all_postsynaptic_targets = np.unique(pre_synapses["post_pt_root_id"])
connectivity_df = pd.DataFrame(
    columns=all_postsynaptic_targets, index=synapses_by_sample.keys()
).fillna(0)
connectivity_df.index.names = ["p_merge", "sample"]

for key, synapses in synapses_by_sample.items():
    for synapse in synapses:
        post_root_id = pre_synapses.loc[synapse, "post_pt_root_id"]
        connectivity_df.loc[key, post_root_id] += 1


# %%
p_found = connectivity_df.sum(axis=0)
p_found = p_found.sort_values(ascending=False)

connectivity_df = connectivity_df[p_found.index]
# %%
fig, ax = plt.subplots(1, 1, figsize=(15, 9))
sns.heatmap(connectivity_df, ax=ax, cmap="Blues", xticklabels=False)

# %%
mtypes = client.materialize.query_table("aibs_metamodel_mtypes_v661_v2")

# %%
mtypes["target_id"].isin(nuc["id"]).mean()

# %%
new_root_ids = mtypes["target_id"].map(nuc.reset_index().set_index("id")["pt_root_id"])
mtypes["root_id"] = new_root_ids

root_id_counts = mtypes["root_id"].value_counts().sort_values(ascending=False)

root_id_dups = root_id_counts[root_id_counts > 1].index

# %%
mtypes = mtypes.query("~root_id.isin(@root_id_dups)")

# %%
mtypes.set_index("root_id", inplace=True)

# %%
connectivity_df.columns.isin(mtypes.index).mean()

# %%

connectivity_df.groupby(by=mtypes["cell_type"], axis=1).sum()

# %%
p_connectivity_df = connectivity_df / connectivity_df.sum(axis=1).values[:, None]

p_connectivity_df.sum(axis=1)

# %%
group_connectivity_df = connectivity_df.groupby(by=mtypes["cell_type"], axis=1).sum()
group_connectivity_df = group_connectivity_df.reindex(
    labels=np.unique(mtypes["cell_type"]), axis=1
).fillna(0)

group_p_connectivity_df = (
    group_connectivity_df / group_connectivity_df.sum(axis=1).values[:, None]
)

# %%
exc_group_p_connectivity_df = group_p_connectivity_df.drop(
    ["DTC", "ITC", "PTC", "STC"], axis=1
)
exc_group_p_connectivity_df = exc_group_p_connectivity_df.sort_index(axis=1)

exc_group_connectivity_df = group_connectivity_df.drop(
    ["DTC", "ITC", "PTC", "STC"], axis=1
)
exc_group_connectivity_df = exc_group_connectivity_df.sort_index(axis=1)
# %%
fig, ax = plt.subplots(1, 1, figsize=(10, 5))
sns.heatmap(exc_group_p_connectivity_df, cmap="Blues", ax=ax)

# %%

palette_file = "data/ctype_hues.pkl"

with open(palette_file, "rb") as f:
    ctype_hues = pickle.load(f)

ctype_hues = {ctype: tuple(ctype_hues[ctype]) for ctype in ctype_hues.keys()}


# %%
exc_group_connectivity_tidy = pd.melt(
    exc_group_connectivity_df.reset_index(),
    id_vars=["p_merge", "sample"],
    value_name="n_synapses",
)
exc_group_p_connectivity_tidy = pd.melt(
    exc_group_p_connectivity_df.reset_index(),
    id_vars=["p_merge", "sample"],
    value_name="p_synapses",
)


fig, axs = plt.subplots(1, 2, figsize=(10, 5))
fig.suptitle(
    f"Root ID {root_id}, Motif group: {query_neurons.set_index('pt_root_id').loc[root_id, 'cell_type']}"
)
plot1 = (
    so.Plot(
        exc_group_connectivity_tidy,
        x="p_merge",
        y="n_synapses",
        color="cell_type",
    )
    .add(so.Dots(pointsize=3, alpha=0.5), so.Jitter())
    .add(so.Line(), so.Agg())
    .add(so.Band(), so.Est())
    .label(
        x="Proportion of filtered merges used",
        y="Number of synapses",
        # title=f"Root ID {root_id}, Motif group: {query_neurons.set_index('pt_root_id').loc[root_id, 'cell_type']}",
        color="Target M-type",
    )
    .layout(engine="tight")
    .scale(color=ctype_hues)
    .on(axs[0])
    # .show()
    .save(f"exc_group_connectivity_root={root_id}.png", bbox_inches="tight")
)
plot2 = (
    so.Plot(
        exc_group_p_connectivity_tidy, x="p_merge", y="p_synapses", color="cell_type"
    )
    .add(so.Dots(pointsize=3, alpha=0.5), so.Jitter())
    .add(so.Line(), so.Agg())
    .add(so.Band(), so.Est())
    .label(
        x="Proportion of filtered merges used",
        y="Proportion of known outputs",
        # title=f"Root ID {root_id}, Motif group: {query_neurons.set_index('pt_root_id').loc[root_id, 'cell_type']}",
        color="Target M-type",
    )
    .layout(engine="tight")
    .scale(color=so.Nominal(ctype_hues))
    .on(axs[1])
    # .show()
    .save(f"exc_group_connectivity_root={root_id}.png", bbox_inches="tight")
)

# %%
