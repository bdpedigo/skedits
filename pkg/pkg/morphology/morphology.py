import caveclient as cc
import numpy as np
import pandas as pd

# import pcg_skel.skel_utils as sk_utils
from meshparty import skeletonize, trimesh_io
from meshparty.skeleton import Skeleton
from navis import TreeNeuron
from networkframe import NetworkFrame

# from pcg_skel.chunk_tools import build_spatial_graph
from sklearn.metrics import pairwise_distances_argmin

from ..utils import get_nucleus_level2_id, get_nucleus_point_nm, get_positions


def skeletonize_networkframe(
    networkframe, client, nan_rounds=10, require_complete=False, soma_pt=None
):
    cv = client.info.segmentation_cloudvolume()

    lvl2_eg = networkframe.edges[["source", "target"]].values.tolist()
    eg, l2dict_mesh, l2dict_r_mesh, x_ch = build_spatial_graph(
        lvl2_eg,
        cv,
        client=client,
        method="service",
        require_complete=require_complete,
    )
    mesh = trimesh_io.Mesh(
        vertices=x_ch,
        faces=[[0, 0, 0]],  # Some functions fail if no faces are set.
        link_edges=eg,
    )

    sk_utils.fix_nan_verts_mesh(mesh, nan_rounds)

    sk = skeletonize.skeletonize_mesh(
        mesh,
        invalidation_d=10_000,
        compute_radius=False,
        cc_vertex_thresh=0,
        remove_zero_length_edges=True,
        soma_pt=soma_pt,
    )
    return sk, mesh, l2dict_mesh, l2dict_r_mesh


def skeleton_to_treeneuron(skeleton: Skeleton):
    f = "tempskel.swc"
    skeleton.export_to_swc(f)
    swc = pd.read_csv(f, sep=" ", header=None)
    swc.columns = ["node_id", "structure", "x", "y", "z", "radius", "parent_id"]
    tn = TreeNeuron(swc)
    return tn


def get_soma_row(
    object_id, client, nuc_table="nucleus_detection_lookup_v1", id_col="pt_root_id"
):
    row = client.materialize.query_view(
        nuc_table, filter_equal_dict={id_col: object_id}
    )
    return row


def get_soma_point(
    object_id,
    client,
    nuc_table="nucleus_detection_lookup_v1",
    id_col="pt_root_id",
    soma_point_resolution=[4, 4, 40],
):
    row = get_soma_row(object_id, client, nuc_table, id_col)
    soma_point = row["pt_position"].values[0]
    soma_point_resolution = np.array(soma_point_resolution)
    soma_point = np.array(soma_point) * soma_point_resolution
    return soma_point


def apply_nucleus(
    nf: NetworkFrame, root_id: int, client: cc.CAVEclient, positional=False
):
    """annotate a point on the level2 graph as the nucleus; whatever is closest"""

    if positional:
        nuc_pt_nm = get_nucleus_point_nm(root_id, client, method="table")

        pos_nodes = nf.nodes[["x", "y", "z"]]
        pos_nodes = pos_nodes[pos_nodes.notna().all(axis=1)]

        ind = pairwise_distances_argmin(nuc_pt_nm.reshape(1, -1), pos_nodes)[0]
        nuc_level2_id = pos_nodes.index[ind]

    else:
        nuc_level2_id = get_nucleus_level2_id(root_id, client)

    nf.nodes["nucleus"] = False
    nf.nodes.loc[nuc_level2_id, "nucleus"] = True
    return nuc_level2_id


def find_component_by_l2_id(nf: NetworkFrame, l2_id: int):
    if l2_id not in nf.nodes.index:
        return None
    query_nf = nf.select_component_from_node(l2_id, directed=False)
    # query_nf = nf.label_nodes_by_component()
    # nuc_component = query_nf.nodes.loc[l2_id, "component"]
    # query_nf = query_nf.query_nodes(f"component == {nuc_component}")
    return query_nf


def apply_positions(nf: NetworkFrame, client: cc.CAVEclient, skip=False):
    node_positions = get_positions(list(nf.nodes.index), client, skip=skip)
    nf.nodes[["rep_coord_nm", "x", "y", "z"]] = node_positions[
        ["rep_coord_nm", "x", "y", "z"]
    ]
