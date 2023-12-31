# %%

import time

t0 = time.time()

import os

import caveclient as cc
import matplotlib.pyplot as plt
from meshparty import meshwork
from networkframe import NetworkFrame
from pcg_skel import features
from skeleton_plot.plot_tools import plot_mw_skel
from tqdm.auto import tqdm

from pkg.edits import (
    apply_edit,
    find_supervoxel_component,
    lazy_load_initial_network,
    lazy_load_network_edits,
)
from pkg.morphology import (
    apply_compartments,
    apply_synapses,
    get_alltime_synapses,
    get_soma_point,
    map_synapses_to_spatial_graph,
    skeletonize_networkframe,
)
from pkg.plot import clean_axis
from pkg.utils import get_level2_nodes_edges

# %%

client = cc.CAVEclient("minnie65_phase3_v1")

# %%
meta = client.materialize.query_table("allen_v1_column_types_slanted_ref")
meta = meta.sort_values("target_id")
nuc = client.materialize.query_table("nucleus_detection_v0").set_index("id")

synapse_table = client.info.get_datastack_info()["synapse_table"]

# %%

os.environ["SKEDITS_USE_CLOUD"] = "True"
os.environ["SKEDITS_RECOMPUTE"] = "False"

# %%
i = 7
target_id = meta.iloc[i]["target_id"]
root_id = nuc.loc[target_id]["pt_root_id"]
root_id = client.chunkedgraph.get_latest_roots(root_id)[0]

# %%
networkdeltas_by_operation, networkdeltas_by_meta_operation = lazy_load_network_edits(
    root_id, client
)

# %%

nf = lazy_load_initial_network(root_id, client, positions=True)


# %%
for metaedit_id, metaedit in tqdm(
    networkdeltas_by_meta_operation.items(), desc="Playing meta-edits"
):
    apply_edit(nf, metaedit)


nuc_supervoxel = nuc.loc[target_id, "pt_supervoxel_id"]
nuc_nf = find_supervoxel_component(nuc_supervoxel, nf, client)


root_nodes, root_edges = get_level2_nodes_edges(root_id, client, positions=False)
root_nf = NetworkFrame(root_nodes, root_edges)

assert root_nf == nuc_nf


soma_point = get_soma_point(root_id, client)
skeleton, mesh, l2dict_mesh, l2dict_r_mesh = skeletonize_networkframe(
    nf, client, soma_pt=soma_point
)

nrn = meshwork.Meshwork(mesh, seg_id=root_id, skeleton=skeleton)
features.add_lvl2_ids(nrn, l2dict_mesh)

plot_mw_skel(nrn, plot_postsyn=False, plot_presyn=False, plot_soma=True)

# %%

pre_synapses, post_synapses = get_alltime_synapses(root_id, client)

pre_synapses, post_synapses = map_synapses_to_spatial_graph(
    pre_synapses, post_synapses, networkdeltas_by_operation, l2dict_mesh, client
)
apply_synapses(nrn, pre_synapses, post_synapses)

# %%
show_plot = False
if show_plot:
    fig, axs = plt.subplots(
        1,
        2,
        figsize=(8, 5),
        sharex=True,
        sharey=True,
        gridspec_kw=dict(hspace=0, wspace=0),
    )

    plot_mw_skel(nrn, plot_postsyn=True, plot_presyn=True, plot_soma=True, ax=axs[0])

    apply_compartments(nrn, root_id, client)

    plot_mw_skel(
        nrn,
        plot_postsyn=True,
        plot_presyn=True,
        plot_soma=True,
        pull_compartment_colors=True,
        ax=axs[1],
    )

    clean_axis(axs[0])
    clean_axis(axs[1])

    axs[0].autoscale()

    axs[0].plot(
        [1.1, 1.1],
        [0.1, 0.9],
        color="darkgrey",
        linestyle="-",
        linewidth=2,
        clip_on=False,
        transform=axs[0].transAxes,
    )

    axs[0].set_title("Before compartment\nlabeling/masking")
    axs[1].set_title("After compartment\nlabeling/masking")
