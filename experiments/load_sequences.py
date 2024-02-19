# %%
import os

os.environ["SKEDITS_USE_CLOUD"] = "True"
os.environ["SKEDITS_RECOMPUTE"] = "False"
os.environ["LAZYCLOUD_RECOMPUTE"] = "False"
os.environ["LAZYCLOUD_USE_CLOUD"] = "True"

import caveclient as cc
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from cloudfiles import CloudFiles
from sklearn.decomposition import PCA
from tqdm.auto import tqdm

from pkg.neuronframe import NeuronFrameSequence, load_neuronframe
from pkg.plot import savefig
from pkg.sequence import create_merge_and_clean_sequence
from pkg.utils import load_casey_palette, load_mtypes

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

# %%

client = cc.CAVEclient("minnie65_phase3_v1")
mtypes = load_mtypes(client)

# %%


def compute_target_stats(seq: NeuronFrameSequence):
    seq.synapse_groupby_count(by="post_mtype", which="pre")
    post_mtype_stats = seq.synapse_groupby_metrics(by="post_mtype", which="pre")
    bouts = seq.sequence_info["has_merge"].fillna(False).cumsum()
    bouts.name = "bout"
    bout_exemplars = (
        seq.sequence_info.index.to_series().groupby(bouts).apply(lambda x: x.iloc[-1])
    )
    # bout_info = seq.sequence_info.loc[bout_exemplars.values]
    bout_post_mtype_stats = post_mtype_stats.query(
        "metaoperation_id.isin(@bout_exemplars)"
    )
    return bout_post_mtype_stats


# %%


root_ids = files_finished["root_id"].unique()
all_targets_stats = {}
all_infos = {}
pbar = tqdm(total=len(root_ids), desc="Computing target stats...")
i = 0
for root_id, rows in files_finished.groupby("root_id"):
    neuron = load_neuronframe(root_id, client)
    neuron.pre_synapses["post_mtype"] = neuron.pre_synapses["post_pt_root_id"].map(
        mtypes["cell_type"]
    )
    for keys, sub_rows in rows.groupby(["order_by", "random_seed"]):
        order_by, random_seed = keys
        if order_by == "time" or order_by == "random":
            sequence = create_merge_and_clean_sequence(
                neuron, root_id, order_by=order_by, random_seed=random_seed
            )
            target_stats = compute_target_stats(sequence)
            target_stats["root_id"] = root_id
            target_stats["order_by"] = order_by
            target_stats["random_seed"] = random_seed
            all_targets_stats[(root_id, order_by, random_seed)] = target_stats

            info = sequence.sequence_info
            info["root_id"] = root_id
            info["order_by"] = order_by
            info["random_seed"] = random_seed
            all_infos[(root_id, order_by, random_seed)] = info

    pbar.update(1)

pbar.close()

# %%
all_target_stats = pd.concat(all_targets_stats.values())
# %%
all_infos = pd.concat(all_infos.values())
# %%

query_neurons = client.materialize.query_table("connectivity_groups_v507")
ctype_map = query_neurons.set_index("pt_root_id")["cell_type"]

# %%

ctype_hues = load_casey_palette()
fig, ax = plt.subplots(1, 1, figsize=(6, 5))
root_id = all_target_stats["root_id"].unique()[12]
root_target_stats = all_target_stats.query("root_id == @root_id")
sns.lineplot(
    data=root_target_stats.query("order_by == 'time'"),
    x="cumulative_n_operations",
    y="prop",
    hue="post_mtype",
    palette=ctype_hues,
    ax=ax,
    legend=False,
    linewidth=3,
)
sns.lineplot(
    data=root_target_stats.query("order_by == 'random'"),
    x="cumulative_n_operations",
    y="prop",
    hue="post_mtype",
    palette=ctype_hues,
    ax=ax,
    legend=False,
    units="random_seed",
    estimator=None,
    linewidth=0.5,
    alpha=0.5,
)
savefig(f"target-stats-random-vs-time-ordered-root_id={root_id}", fig)


# %%
# TODO pivot or pivot table
X_df = all_target_stats.pivot_table(
    index=["root_id", "order_by", "random_seed", "metaoperation_id"],
    columns="post_mtype",
    values="prop",
).fillna(0)
print(X_df.shape)
# %%
X_df.loc[X_df.index.get_level_values("metaoperation_id").isna()]
# %%
all_target_stats


# %%
X = X_df.values

hue = X_df.index.get_level_values("root_id").map(ctype_map).astype(str)
hue.name = "C-Type"

# %%
pca = PCA(n_components=6)
X_pca = pca.fit_transform(X)

# TODO melt this back to the same format as all_target_stats and join with that

# %%
X_pca_df = pd.DataFrame(
    X_pca, columns=["PC1", "PC2", "PC3", "PC4", "PC5", "PC6"], index=X_df.index
).reset_index(drop=False)
X_pca_df
# %%
all_infos = all_infos.reset_index(drop=False)
all_infos = all_infos.set_index(
    ["root_id", "order_by", "random_seed", "metaoperation_id"]
)
all_infos
# %%
X_pca_df = X_pca_df.set_index(
    ["root_id", "order_by", "random_seed", "metaoperation_id"]
)
# %%
X_pca_df = X_pca_df.join(all_infos)
# %%
X_pca_df = X_pca_df.reset_index(drop=False)
# %%
X_pca_df["ctype"] = X_pca_df["root_id"].map(ctype_map).astype(str)

# %%

fig, ax = plt.subplots(1, 1, figsize=(6, 5))

sns.scatterplot(
    data=X_pca_df,
    x="PC1",
    y="PC2",
    hue="ctype",
    s=1,
    linewidth=0,
    alpha=0.3,
    legend=False,
)

# %%
# %%
from sklearn.cluster import KMeans

kmeans = KMeans(n_clusters=3).fit(X_pca)

# %%
centers = kmeans.cluster_centers_

# %%
native_centers = pca.inverse_transform(centers)

native_centers_df = pd.DataFrame(
    native_centers, columns=X_df.columns, index=[f"Cluster {i}" for i in range(3)]
)
sns.heatmap(native_centers_df, cmap="RdBu_r", center=0, annot=False)

# %%
X_pca_df["root_id_str"] = X_pca_df["root_id"].astype(str)
fig, ax = plt.subplots(1, 1, figsize=(6, 5))

x = 1
y = 2
sns.scatterplot(
    data=X_pca_df,
    x=f"PC{x}",
    y=f"PC{y}",
    s=1,
    linewidth=0,
    alpha=0.3,
    legend=False,
    color="lightgrey",
)

sns.scatterplot(
    data=X_pca_df.query("ctype == '1'"),
    x=f"PC{x}",
    y=f"PC{y}",
    s=2,
    hue="root_id_str",
    legend=False,
    ax=ax,
)

centers_df = pd.DataFrame(
    centers,
    columns=[f"PC{i}" for i in range(1, 7)],
)
for i, row in centers_df.iterrows():
    ax.text(row[f"PC{x}"], row[f"PC{y}"], i, fontsize=12, ha="center", va="bottom")
    ax.scatter(row[f"PC{x}"], row[f"PC{y}"], s=100, color="black", marker="*")




# %%

fig, ax = plt.subplots(1, 1, figsize=(6, 5))
from umap import UMAP

umap = UMAP(
    n_components=2, min_dist=0.6, n_neighbors=25, random_state=0, metric="cosine"
)
X_umap = umap.fit_transform(X)

sns.scatterplot(
    x=X_umap[:, 0],
    y=X_umap[:, 1],
    hue=hue,
    ax=ax,
    legend=True,
    s=1,
    linewidth=0,
)

# %%

all_target_stats["ctype"] = all_target_stats["root_id"].map(ctype_map)

# %%
sub_target_stats = all_target_stats.query("ctype == 1")

fig, ax = plt.subplots(1, 1, figsize=(6, 5))
sns.lineplot(
    data=sub_target_stats.query("order_by == 'random'").reset_index(drop=False),
    x="cumulative_n_operations",
    y="prop",
    hue="post_mtype",
    palette=ctype_hues,
    ax=ax,
    legend=False,
    linewidth=1,
    units="random_seed",
    estimator=None,
)
