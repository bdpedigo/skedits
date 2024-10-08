# %%

import pickle

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyvista as pv
import seaborn as sns
from neurovista import center_camera
from scipy.spatial.distance import cdist
from sklearn.metrics import pairwise_distances_argmin_min

from pkg.constants import MERGE_COLOR, OUT_PATH, SPLIT_COLOR
from pkg.plot import savefig, set_context
from pkg.utils import get_nucleus_point_nm, load_manifest, load_mtypes, start_client

set_context()

client = start_client()
mtypes = load_mtypes(client)

distance_colors = sns.color_palette("Set1", n_colors=5)
distance_palette = dict(
    zip(
        ["euclidean", "cityblock", "jensenshannon", "cosine", "hamming"],
        distance_colors,
    )
)
# TODO whether to implement this as a table of tables, one massive table...
# nothing really feels satisfying here
# perhaps a table of tables will work, and it can infill the index onto those tables
# before doing a join or concat

# TODO key the elements in the sequence on something other than metaoperation_id, this
# will make it easier to join with the time-ordered dataframes which use "operation_id",
# or do things like take "bouts" for computing metrics which are not tied to a specific
# operation_id

with open(OUT_PATH / "sequence_metrics" / "all_infos.pkl", "rb") as f:
    all_infos = pickle.load(f)
    all_infos = all_infos.set_index(
        ["root_id", "scheme", "order_by", "random_seed", "order"]
    )
with open(OUT_PATH / "sequence_metrics" / "meta_features_df.pkl", "rb") as f:
    meta_features_df = pickle.load(f)

manifest = load_manifest()
manifest = manifest.query("in_inhibitory_column")


# %%


def compute_diffs_to_final(sequence_df):
    # sequence = sequence_df.index.get_level_values("sequence").unique()[0]
    final_row_idx = sequence_df.index.get_level_values("order").max()
    final_row = sequence_df.loc[final_row_idx].fillna(0).values.reshape(1, -1)
    X = sequence_df.fillna(0).values

    sample_wise_metrics = []
    for metric in ["euclidean", "cityblock", "jensenshannon", "cosine", "hamming"]:
        distances = cdist(X, final_row, metric=metric)
        distances = pd.Series(
            distances.flatten(),
            name=metric,
            index=sequence_df.index.get_level_values("order"),
        )
        sample_wise_metrics.append(distances)
    sample_wise_metrics = pd.concat(sample_wise_metrics, axis=1)

    return sample_wise_metrics


# %%
precision_recall_df = pd.concat(meta_features_df["pre_precision_recall"].to_list())[
    ["pre_synapse_recall", "pre_synapse_precision"]
]


precision_recall_df["f1"] = (
    2
    * precision_recall_df["pre_synapse_precision"]
    * precision_recall_df["pre_synapse_recall"]
    / (
        precision_recall_df["pre_synapse_precision"]
        + precision_recall_df["pre_synapse_recall"]
    ).replace(0, 1)
)


# scheme = "historical"
scheme = "lumped-time"

idx = pd.IndexSlice

if scheme == "historical" or scheme == "lumped-time":
    precision_recall_df = precision_recall_df.loc[idx[:, [scheme], :, :]]
    info = all_infos.loc[idx[:, scheme, :, :, :]]
    precision_recall_df = precision_recall_df.droplevel(["order_by", "random_seed"])
    info = info.droplevel(["order_by", "random_seed"])
    precision_recall_df = precision_recall_df.join(
        info, on=["root_id", "order"]
    ).droplevel("scheme")
    precision_recall_df.index.rename({None: "operation_id"}, inplace=True)
elif scheme == "clean-and-merge-time":
    precision_recall_df = precision_recall_df.loc[
        idx[:, ["clean-and-merge"], ["time"], :]
    ]
    info = all_infos.loc[idx[:, "clean-and-merge", "time", :, :]]
    precision_recall_df = precision_recall_df.droplevel(["random_seed"])
    info = info.droplevel("random_seed")
    precision_recall_df = precision_recall_df.join(
        info, on=["root_id", "order"]
    ).droplevel(["scheme", "order_by"])
    precision_recall_df = precision_recall_df.drop("metaoperation_id", axis=1)
    precision_recall_df.index.rename({None: "metaoperation_id"}, inplace=True)
    # precision_recall_df.index.rename()
elif scheme == "clean-and-merge-random":
    precision_recall_df = precision_recall_df.loc[
        idx[:, "clean-and-merge", "random", :]
    ]


set_context(font_scale=2)

edit_palette = {True: MERGE_COLOR, False: SPLIT_COLOR}
colors = sns.color_palette("Set1")
colors = [colors[0], colors[1], colors[3]]
x = "cumulative_n_operations"
for i, root_id in enumerate(manifest.query("is_example").index):
    df = precision_recall_df.loc[root_id].copy()
    # df["is_filtered"] = df["is_filtered"].fillna(True)
    # df = df.query("is_filtered")
    # df["filtered_order"] = np.arange(df.shape[0])
    df["x"] = df["n_operations"].cumsum()

    fig, ax = plt.subplots(
        1,
        1,
        figsize=(6, 6),
        # gridspec_kw={"height_ratios": [1, 0.05], "hspace": 0.01},
        # sharex=True,
    )
    # ax = axs[0]
    sns.lineplot(
        data=df.reset_index(),
        x="x",
        y="pre_synapse_precision",
        color=colors[0],
        label="Precision",
        linestyle=":",
        linewidth=2,
        ax=ax,
    )
    sns.lineplot(
        data=df.reset_index(),
        x="x",
        y="pre_synapse_recall",
        color=colors[1],
        label="Recall",
        linestyle="--",
        linewidth=2,
        ax=ax,
    )
    sns.lineplot(
        data=df.reset_index(),
        x="x",
        y="f1",
        color=colors[2],
        label="F1",
        linewidth=3,
        ax=ax,
    )
    ax.legend()
    ax.set(ylabel="Score", xlabel="State", ylim=(0, 1.01), xlim=(-0.2, df["x"].max()))

    # turn off xticks for this axis, but have to change the length of the ticks
    # since the xaxis is shared
    # ax.tick_params(length=0, axis='x')

    # ax = axs[1]
    # ax = axs[0]
    # is_merge = df["has_merge"].values[1:].astype(bool)
    # for i in range(len(is_merge)):
    #     indicator = is_merge[i]
    #     ax.fill_between(
    #         [i, i + 1],
    #         0,
    #         1.01,
    #         color=edit_palette[indicator],
    #         alpha=0.2,
    #         linewidth=0,
    #         # hatch="." if indicator else "",
    #         # where=is_merge[i],
    #     )
    counter = 0
    for i, edit_row in df.iterrows():
        operation_id = i[0]
        order = i[1]
        if order == 0:
            continue
        if scheme == "historical":
            is_merges = [edit_row["is_merge"]]
        else:
            is_merges = edit_row["is_merges"]

        for is_merge in is_merges:
            ax.fill_between(
                [counter, counter + 1],
                0,
                1.01,
                color=edit_palette[is_merge],
                alpha=0.2,
                linewidth=0,
            )
            counter += 1

    # ax = axs[1]
    # ax.set(yticks=[], xlabel="State")
    # ax.spines["left"].set_visible(False)
    target_id = manifest.loc[root_id, "target_id"]
    savefig(
        f"precision_recall_target={target_id}_scheme={scheme}",
        fig,
        folder="precision_recall",
    )
    savefig(
        f"precision_recall_target={target_id}_scheme={scheme}",
        fig,
        folder="precision_recall",
        doc_save=True,
        format="svg",
    )


fig, axs = plt.subplots(4, 5, figsize=(20, 16), sharey=True, layout="constrained")
for i, root_id in enumerate(manifest.query("is_example").index):
    ax = axs.flat[i]

    df = precision_recall_df.loc[root_id].copy()

    df["x"] = df["n_operations"].cumsum()

    sns.lineplot(
        data=df.reset_index(),
        x="x",
        y="pre_synapse_precision",
        color=colors[0],
        label="Precision",
        linestyle=":",
        linewidth=2,
        ax=ax,
    )
    sns.lineplot(
        data=df.reset_index(),
        x="x",
        y="pre_synapse_recall",
        color=colors[1],
        label="Recall",
        linestyle="--",
        linewidth=2,
        ax=ax,
    )
    sns.lineplot(
        data=df.reset_index(),
        x="x",
        y="f1",
        color=colors[2],
        label="F1",
        linewidth=3,
        ax=ax,
    )
    if i == 0:
        ax.legend()
    else:
        ax.get_legend().remove()
    ax.set(ylabel="Score", xlabel="State", ylim=(0, 1.01), xlim=(-0.2, df["x"].max()))

    counter = 0
    for i, edit_row in df.iterrows():
        operation_id = i[0]
        order = i[1]
        if order == 0:
            continue
        if scheme == "historical":
            is_merges = [edit_row["is_merge"]]
        else:
            is_merges = edit_row["is_merges"]
        for is_merge in is_merges:
            ax.fill_between(
                [counter, counter + 1],
                0,
                1.01,
                color=edit_palette[is_merge],
                alpha=0.2,
                linewidth=0,
            )
            counter += 1

savefig(
    f"precision_recall_gallery_scheme={scheme}",
    fig,
    folder="precision_recall",
)

# %%

set_context(font_scale=2)
fig, ax = plt.subplots(1, 1, figsize=(6, 6))
sns.lineplot(
    data=precision_recall_df.reset_index(),
    x="cumulative_n_operations",
    y="pre_synapse_precision",
    estimator=None,
    units="root_id",
    alpha=0.2,
    color="black",
)
ax.set(ylabel="Precision", xlabel="Number of operations")
savefig(f"precision_vs_operations_scheme={scheme}", fig)

fig, ax = plt.subplots(1, 1, figsize=(6, 6))
sns.lineplot(
    data=precision_recall_df.reset_index(),
    x="cumulative_n_operations",
    y="pre_synapse_recall",
    estimator=None,
    units="root_id",
    alpha=0.2,
    color="black",
)
ax.set(ylabel="Recall", xlabel="Number of operations")
savefig(f"recall_vs_operations_scheme={scheme}", fig)

fig, ax = plt.subplots(1, 1, figsize=(6, 6))
sns.lineplot(
    data=precision_recall_df.reset_index(),
    x="cumulative_n_operations",
    y="f1",
    estimator=None,
    units="root_id",
    alpha=0.2,
    color="black",
)
ax.set(ylabel="F1", xlabel="Number of operations")
savefig(f"f1_vs_operations_scheme={scheme}", fig)


# %%
precision_recall_df["p_operations"] = precision_recall_df[
    "cumulative_n_operations"
] / precision_recall_df.groupby("root_id")["cumulative_n_operations"].transform("max")

fig, ax = plt.subplots(1, 1, figsize=(6, 6))
sns.lineplot(
    data=precision_recall_df.reset_index(),
    x="p_operations",
    y="pre_synapse_precision",
    estimator=None,
    units="root_id",
    alpha=0.2,
    color="black",
)
ax.set(ylabel="Precision", xlabel="Proportion of operations")
savefig(f"precision_vs_p_operations_scheme={scheme}", fig)

fig, ax = plt.subplots(1, 1, figsize=(6, 6))
sns.lineplot(
    data=precision_recall_df.reset_index(),
    x="p_operations",
    y="pre_synapse_recall",
    estimator=None,
    units="root_id",
    alpha=0.2,
    color="black",
)
ax.set(ylabel="Recall", xlabel="Proportion of operations")
savefig(f"recall_vs_p_operations_scheme={scheme}", fig)


fig, ax = plt.subplots(1, 1, figsize=(6, 6))
sns.lineplot(
    data=precision_recall_df.reset_index(),
    x="p_operations",
    y="f1",
    estimator=None,
    units="root_id",
    alpha=0.2,
    color="black",
)
ax.set(ylabel="F1", xlabel="Proportion of operations")
savefig(f"f1_vs_p_operations_scheme={scheme}", fig)


# %%

for i, root_id in enumerate(manifest.query("is_example").index):
    df = precision_recall_df.loc[root_id].copy()
    df["is_filtered"] = df["is_filtered"].fillna(True)
    df = df.query("is_filtered")
    df["filtered_order"] = np.arange(df.shape[0])


# %%
from pkg.neuronframe import load_neuronframe

root_id = manifest.query("is_example").index[12]

df = precision_recall_df.loc[root_id]

fig, ax = plt.subplots(1, 1, figsize=(6, 6))

sns.lineplot(
    data=df.reset_index(),
    x="order",
    y="precision",
    color=colors[0],
    label="Precision",
    linestyle=":",
    linewidth=2,
    ax=ax,
)
sns.lineplot(
    data=df.reset_index(),
    x="order",
    y="recall",
    color=colors[1],
    label="Recall",
    linestyle="--",
    linewidth=2,
    ax=ax,
)
sns.lineplot(
    data=df.reset_index(),
    x="order",
    y="f1",
    color=colors[2],
    label="F1",
    linewidth=3,
    ax=ax,
)
ax.legend()
ax.set(ylabel="Score", xlabel="State", ylim=(0, 1.01))

# indices = [19]
# pad = 1
# for index in indices:
#     # ax.axvline(index, color="lightgrey", linestyle="-", zorder=-1)
#     ax.fill_between(
#         [index - pad, index + pad],
#         0,
#         1,
#         color="lightgrey",
#         alpha=0.5,
#         zorder=-1,
#         label="Example",
#     )


# %%
nf = load_neuronframe(root_id, client)

# %%
nf.edits.head(20)

# root ID 864691135697284250
# operations 201595	and 207960 are inverses of each other
# axon detachment and reattachment

# %%
cv = client.info.segmentation_cloudvolume()
cv.cache.enabled = True

# %%
from neurovista import to_mesh_polydata

edit_row = nf.edits.loc[df.index[10][0]]

before_root_ids = edit_row["before_root_ids"]
after_root_ids = edit_row["roots"]
relevant_root_ids = before_root_ids + after_root_ids

meshes = cv.mesh.get(relevant_root_ids)

# %%

nuc_loc = get_nucleus_point_nm(root_id, client)

center = edit_row[["centroid_x", "centroid_y", "centroid_z"]].values
pv.set_jupyter_backend("client")
plotter = pv.Plotter()
mesh_polys = {}
# for i, (this_root_id, mesh) in enumerate(meshes.items()):
colors = sns.color_palette("Accent", n_colors=len(relevant_root_ids))
palette = dict(zip(relevant_root_ids, colors))
min_dists = []
for i, this_root_id in enumerate(before_root_ids):
    mesh = meshes[this_root_id]
    mesh_poly = to_mesh_polydata(mesh.vertices, mesh.faces)

    _, dist = pairwise_distances_argmin_min([nuc_loc], mesh.vertices)
    dist = np.squeeze(dist)
    min_dists.append(dist)

    mesh_polys[this_root_id] = mesh_poly

nuc_root_index = np.argmin(min_dists)
nuc_root_id = before_root_ids[nuc_root_index]

nuc_mesh = mesh_polys[nuc_root_id]
nonnuc_mesh = mesh_polys[before_root_ids[1 - nuc_root_index]]

lighting = True
plotter.add_mesh(nuc_mesh, color="lightgrey", smooth_shading=True, lighting=lighting)
plotter.add_mesh(nonnuc_mesh, color=MERGE_COLOR, smooth_shading=True, lighting=lighting)

center_camera(plotter, center, distance=50_000)
plotter.link_views()
plotter.show()

# %%
