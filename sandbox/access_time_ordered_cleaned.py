# %%
import os

os.environ["LAZYCLOUD_USE_CLOUD"] = "True"
os.environ["LAZYCLOUD_RECOMPUTE"] = "False"
os.environ["SKEDITS_USE_CLOUD"] = "True"
os.environ["SKEDITS_RECOMPUTE"] = "False"


import caveclient as cc
import matplotlib.pyplot as plt
import pandas as pd
import pyvista as pv
import seaborn as sns
from scipy.spatial.distance import cdist
from tqdm.auto import tqdm

from pkg.constants import FIG_PATH, OUT_PATH
from pkg.edits import count_synapses_by_sample
from pkg.neuronframe import load_neuronframe, verify_neuron_matches_final
from pkg.plot import savefig
from pkg.utils import find_closest_point, load_casey_palette, load_mtypes

pv.set_jupyter_backend("client")

# %%


def apply_operations(
    full_neuron,
    applied_op_ids,
    resolved_synapses,
    neuron_list,
    operation_key,
    iteration_key,
):
    current_neuron = full_neuron.set_edits(applied_op_ids, inplace=False, prefix=prefix)

    if full_neuron.nucleus_id in current_neuron.nodes.index:
        current_neuron.select_nucleus_component(inplace=True)
    else:
        print("WARNING: Using closest point to nucleus...")
        point_id = find_closest_point(
            current_neuron.nodes,
            full_neuron.nodes.loc[full_neuron.nucleus_id, ["x", "y", "z"]],
        )
        current_neuron.select_component_from_node(
            point_id, inplace=True, directed=False
        )

    current_neuron.remove_unused_synapses(inplace=True)

    neuron_list[iteration_key] = current_neuron
    resolved_synapses[iteration_key] = {
        "resolved_pre_synapses": current_neuron.pre_synapses.index.to_list(),
        "resolved_post_synapses": current_neuron.post_synapses.index.to_list(),
        operation_key: applied_op_ids[-1] if i > 0 else None,
    }

    return current_neuron


def select_next_operation(full_neuron, current_neuron, applied_op_ids, possible_op_ids):
    # select the next operation to apply
    # this looks at all of the edges that are connected to the current neuron
    # and then finds the set of operations that are "touched" by this one
    # then it selects the first one of those that hasn't been applied yet, in time
    # order
    out_edges = full_neuron.edges.query(
        "source.isin(@current_neuron.nodes.index) | target.isin(@current_neuron.nodes.index)"
    )
    out_edges = out_edges.drop(current_neuron.edges.index)

    candidate_operations = out_edges[operation_key].unique()

    # TODO this is hard coded
    ordered_ops = possible_op_ids[possible_op_ids.isin(candidate_operations)]

    # HACK?
    # TODO should this be applied merges, or applied ops?
    ordered_ops = ordered_ops[~ordered_ops.isin(applied_merges)]

    if len(ordered_ops) == 0:
        return False
    else:
        applied_op_ids.append(ordered_ops[0])
        applied_merges.append(ordered_ops[0])
        return True


client = cc.CAVEclient("minnie65_phase3_v1")

query_neurons = client.materialize.query_table("connectivity_groups_v507")
query_neurons.sort_values("id", inplace=True)

prefix = ""
path = OUT_PATH / "access_time_ordered"
completes_neuron = False

ctype_hues = load_casey_palette()

root_id = query_neurons["pt_root_id"].values[1]

full_neuron = load_neuronframe(root_id, client)


# %%

import numpy as np

# static plot of the initial state of the neuron

skeleton_poly = full_neuron.to_skeleton_polydata()

pl = pv.Plotter()
nuc_loc = full_neuron.nodes.loc[full_neuron.nucleus_id, ["x", "y", "z"]].values

pl.camera_position = "zx"

setback = -2_000_000
pl.camera.focal_point = nuc_loc
pl.camera.position = nuc_loc + np.array([0, 0, setback])
pl.camera.up = (0, -1, 0)
pl.camera.azimuth += 200

pl.add_mesh(skeleton_poly, color="black", line_width=1)


# pl.camera.roll = 0

# pl.camera.elevation = 45
# pl.camera.view_angle = 0


pl.show()

pl.camera_position

# %%
edits = full_neuron.edits
edits.sort_values("time", inplace=True)

fps = 20
# window_size = (1920, 1080)
window_size = None
n_rotation_steps = 5
azimuth_step_size = 1

plotter = pv.Plotter(window_size=window_size)


# plotter.camera_position = "zx"
# plotter.camera.focal_point = nuc_loc
# plotter.camera.position = nuc_loc + np.array([0, 0, setback])
# plotter.camera.up = (0, -1, 0)


print(plotter.camera_position)

plotter.open_gif(
    str(FIG_PATH / "animations" / f"all_edits-root_id={root_id}.gif"), fps=fps
)

setback = -2_000_000
nuc_loc = full_neuron.nodes.loc[full_neuron.nucleus_id, ["x", "y", "z"]].values
# print(plotter.camera_position)

skeleton_poly = full_neuron.to_skeleton_polydata()
skeleton_actor = plotter.add_mesh(skeleton_poly, color="black", line_width=0.1)
plotter.camera_position = "zx"
plotter.camera.focal_point = nuc_loc
plotter.camera.position = nuc_loc + np.array([0, 0, setback])
plotter.camera.up = (0, -1, 0)

plotter.remove_actor(skeleton_actor)

last_nodes = pd.DataFrame()

actors_remove_queue = []

for i in tqdm(range(len(edits[:])), desc="Applying edits..."):
    current_neuron = full_neuron.set_edits(edits.index[:i], inplace=False, prefix="")

    # point_id = find_closest_point(
    #     current_neuron.nodes,
    #     full_neuron.nodes.loc[full_neuron.nucleus_id, ["x", "y", "z"]],
    # )
    current_neuron.select_component_from_node(
        full_neuron.nucleus_id, inplace=True, directed=False
    )

    skeleton_poly = current_neuron.to_skeleton_polydata()
    skeleton_actor = plotter.add_mesh(skeleton_poly, color="black", line_width=1)

    highlight = current_neuron.nodes.index.difference(last_nodes.index)
    if len(highlight) > 0:
        highlight_poly = current_neuron.query_nodes(
            "index.isin(@highlight)", local_dict=locals()
        ).to_skeleton_polydata()
        highlight_actor = plotter.add_mesh(
            highlight_poly, color="purple", point_size=8, line_width=3
        )
        username = edits.iloc[i]["user_name"]
        text = pv.Text3D(
            username,
            center=highlight_poly.center,
        )

        text_actor = plotter.add_mesh(text)

        actors_remove_queue.append((highlight_actor, text_actor))

    merge_poly, split_poly = current_neuron.to_edit_polydata()
    if len(merge_poly.points) > 0:
        merge_actor = plotter.add_mesh(merge_poly, color="purple", point_size=4)

    if len(split_poly.points) > 0:
        split_actor = plotter.add_mesh(split_poly, color="red", point_size=4)

    time_actor = plotter.add_text(
        "t = " + str(edits.iloc[i]["time"]), position="upper_left"
    )

    for _ in range(n_rotation_steps):
        plotter.camera.azimuth += azimuth_step_size
        plotter.write_frame()

    plotter.remove_actor(time_actor)

    plotter.remove_actor(skeleton_actor)

    if len(actors_remove_queue) > 5:
        for actor in actors_remove_queue.pop(0):
            plotter.remove_actor(actor)

    if len(merge_poly.points) > 0:
        plotter.remove_actor(merge_actor)
    if len(split_poly.points) > 0:
        plotter.remove_actor(split_actor)

    last_nodes = current_neuron.nodes

print("Closing gif...")
plotter.close()
# current_neuron.remove_unused_synapses(inplace=True)

# %%

if prefix == "meta":
    edits = full_neuron.metaedits
else:
    edits = full_neuron.edits
    edits["has_split"] = ~edits["is_merge"]
    edits["has_merge"] = edits["is_merge"]

edits = edits.sort_values("time")
# edits["any_merge"] = edits["is_merges"].apply(any)

split_metaedits = edits.query("has_split")
merge_metaedits = edits.query("has_merge")
merge_op_ids = merge_metaedits.index
split_op_ids = split_metaedits.index
applied_op_ids = list(split_op_ids)

# edge case where the neuron's ultimate soma location is itself a merge node
operation_key = f"{prefix}operation_added"
if full_neuron.nodes.loc[full_neuron.nucleus_id, operation_key] != -1:
    applied_op_ids.append(full_neuron.nodes.loc[full_neuron.nucleus_id, operation_key])


neurons = {}
resolved_synapses = {}
applied_merges = []


for i in tqdm(
    range(len(merge_op_ids) + 1), desc="Applying edits and resolving synapses..."
):
    # TODO consider doing this in a way such that I can keep track of how many different
    # split edits are applied with each merge
    # i think this would just mean recursively adding the split edits if available, and
    # then keeping track of merges when they pop up.
    current_neuron = apply_operations(
        full_neuron,
        applied_op_ids,
        resolved_synapses,
        neurons,
        operation_key,
        i,
    )

    # TODO write this in a way where this part can be swapped in and out
    more_operations = select_next_operation(
        full_neuron, current_neuron, applied_op_ids, merge_op_ids
    )
    if not more_operations:
        break

# print(pl.camera_position)


print(f"No remaining merges, stopping ({i / len(merge_op_ids):.2f})")

resolved_synapses = pd.DataFrame(resolved_synapses).T

if completes_neuron:
    verify_neuron_matches_final(full_neuron, current_neuron)

# %%

# generate an animation


# Open a gif

fps = 20
window_size = (1920, 1080)
n_rotation_steps = 20
azimuth_step_size = 1

plotter = pv.Plotter(window_size=window_size)

plotter.open_gif(str(FIG_PATH / "animations" / f"edits-root_id={root_id}.gif"), fps=fps)

for sample_id, neuron in tqdm(neurons.items(), desc="Writing frames..."):
    # TODO there might be a smarter way to do this with masking, but this seems fast
    actor = plotter.add_mesh(
        current_neuron.to_skeleton_polydata(), color="black", line_width=1
    )
    merge_poly, split_poly = current_neuron.to_edit_polydata()
    merge_actor = plotter.add_mesh(merge_poly, color="purple", point_size=4)
    split_actor = plotter.add_mesh(split_poly, color="red", point_size=4)

    for _ in range(n_rotation_steps):
        plotter.camera.azimuth += azimuth_step_size
        plotter.write_frame()

    plotter.remove_actor(actor)
    plotter.remove_actor(merge_actor)
    plotter.remove_actor(split_actor)

print("Closing gif...")
plotter.close()

# %%


# %%
def compute_synapse_metrics(
    full_neuron,
    edits,
    resolved_synapses,
    operation_key,
):
    mtypes = load_mtypes(client)

    pre_synapses = full_neuron.pre_synapses
    # map post synapses to their mtypes
    pre_synapses["post_mtype"] = pre_synapses["post_pt_root_id"].map(
        mtypes["cell_type"]
    )

    # post_synapses = full_neuron.post_synapses
    # post_synapses["pre_mtype"] = post_synapses["pre_pt_root_id"].map(
    #     mtypes["cell_type"]
    # )

    # find the synapses per sample
    resolved_pre_synapses = resolved_synapses["resolved_pre_synapses"]
    post_mtype_counts = count_synapses_by_sample(
        pre_synapses, resolved_pre_synapses, "post_mtype"
    )

    # wrangle counts and probs
    counts_table = post_mtype_counts
    var_name = "post_mtype"
    post_mtype_stats_tidy = counts_table.reset_index().melt(
        var_name=var_name, value_name="count", id_vars="sample"
    )
    post_mtype_probs = counts_table / counts_table.sum(axis=1).values[:, None]
    post_mtype_probs.fillna(0, inplace=True)
    post_mtype_probs_tidy = post_mtype_probs.reset_index().melt(
        var_name=var_name, value_name="prob", id_vars="sample"
    )
    post_mtype_stats_tidy["prob"] = post_mtype_probs_tidy["prob"]
    post_mtype_stats_tidy[operation_key] = post_mtype_stats_tidy["sample"].map(
        resolved_synapses[operation_key]
    )
    post_mtype_stats_tidy = post_mtype_stats_tidy.join(edits, on=operation_key)

    final_probs = post_mtype_probs.iloc[-1]

    # euclidean distance
    # euc_diffs = (((post_mtype_probs - final_probs) ** 2).sum(axis=1)) ** 0.5

    sample_wise_metrics = []
    for metric in ["euclidean", "cityblock", "jensenshannon", "cosine"]:
        distances = cdist(
            post_mtype_probs.values, final_probs.values.reshape(1, -1), metric=metric
        )
        distances = pd.Series(
            distances.flatten(), name=metric, index=post_mtype_probs.index
        )
        sample_wise_metrics.append(distances)
    sample_wise_metrics = pd.concat(sample_wise_metrics, axis=1)
    sample_wise_metrics[operation_key] = sample_wise_metrics.index.map(
        resolved_synapses[operation_key]
    )
    sample_wise_metrics = sample_wise_metrics.join(edits, on=operation_key)

    # TODO might as well also do the same join as the above to the added metaedits

    return post_mtype_stats_tidy, sample_wise_metrics


# %%
post_mtype_stats_tidy, sample_wise_metrics = compute_synapse_metrics(
    full_neuron, edits, resolved_synapses, operation_key
)

# %%
metrics = ["euclidean", "cityblock", "jensenshannon", "cosine"]
n_col = len(metrics)

fig, axs = plt.subplots(1, n_col, figsize=(5 * n_col, 5))

for i, metric in enumerate(metrics):
    sns.lineplot(
        data=sample_wise_metrics,
        x="sample",
        y=metric,
        ax=axs[i],
    )
    axs[i].set_xlabel("Metaoperation added")
    axs[i].set_ylabel(f"{metric} distance")
    axs[i].spines[["top", "right"]].set_visible(False)


# %%
save = False

sns.set_context("talk")

fig, ax = plt.subplots(1, 1, figsize=(6, 5))

sns.lineplot(
    data=post_mtype_stats_tidy,
    x="sample",
    y="count",
    hue="post_mtype",
    legend=False,
    palette=ctype_hues,
    ax=ax,
)
ax.set_xlabel("Metaoperation added")
ax.set_ylabel("# output synapses")
ax.spines[["top", "right"]].set_visible(False)
if save:
    savefig(
        f"output_synapses_access_time_ordered-root_id={root_id}",
        fig,
        folder="access_time_ordered",
    )

fig, ax = plt.subplots(1, 1, figsize=(6, 5))

sns.lineplot(
    data=post_mtype_stats_tidy,
    x="sample",
    y="prob",
    hue="post_mtype",
    legend=False,
    palette=ctype_hues,
    ax=ax,
)
ax.set_xlabel("Metaoperation added")
ax.set_ylabel("Proportion of output synapses")
ax.spines[["top", "right"]].set_visible(False)

if save:
    savefig(
        f"output_proportion_access_time_ordered-root_id={root_id}",
        fig,
        folder="access_time_ordered",
    )


fig, ax = plt.subplots(1, 1, figsize=(6, 5))

sns.lineplot(
    data=post_mtype_stats_tidy,
    x="sample",
    y="centroid_distance_to_nuc_um",
    hue="post_mtype",
    legend=False,
    palette=ctype_hues,
    ax=ax,
)
ax.set_xlabel("Metaoperation added")
ax.set_ylabel("Distance to nucleus (nm)")
ax.spines[["top", "right"]].set_visible(False)

if save:
    savefig(
        f"distance_access_time_ordered-root_id={root_id}",
        fig,
        folder="access_time_ordered",
    )

fig, ax = plt.subplots(1, 1, figsize=(6, 5))

sns.lineplot(
    data=diffs,
    x="sample",
    y="diff",
)
ax.set_xlabel("Metaoperation added")
ax.set_ylabel("Distance from final")
ax.spines[["top", "right"]].set_visible(False)

if save:
    savefig(
        f"distance_from_final_access_time_ordered-root_id={root_id}",
        fig,
        folder="access_time_ordered",
    )

if save:
    resolved_synapses.to_csv(path / f"resolved_synapses-root_id={root_id}.csv")

    post_mtype_stats_tidy.to_csv(path / f"post_mtype_stats_tidy-root_id={root_id}.csv")

    diffs.to_csv(path / f"diffs-root_id={root_id}.csv")

# %%
