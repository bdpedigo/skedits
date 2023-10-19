import networkx as nx
import pandas as pd
import tqdm

from ..utils import get_all_nodes_edges


def get_changed_edges(before_edges, after_edges):
    before_edges.drop_duplicates()
    before_edges["is_before"] = True
    after_edges.drop_duplicates()
    after_edges["is_before"] = False
    delta_edges = pd.concat([before_edges, after_edges]).drop_duplicates(
        ["source", "target"], keep=False
    )
    removed_edges = delta_edges.query("is_before").drop(columns=["is_before"])
    added_edges = delta_edges.query("~is_before").drop(columns=["is_before"])
    return removed_edges, added_edges


def get_detailed_change_log(root_id, client, filtered=True):
    cg = client.chunkedgraph
    change_log = cg.get_tabular_change_log(root_id, filtered=filtered)[root_id]

    change_log.set_index("operation_id", inplace=True)
    change_log.sort_values("timestamp", inplace=True)
    change_log.drop(columns=["timestamp"], inplace=True)

    details = cg.get_operation_details(change_log.index.to_list())
    details = pd.DataFrame(details).T
    details.index.name = "operation_id"
    details.index = details.index.astype(int)

    change_log = change_log.join(details)

    return change_log


class NetworkDelta:
    # TODO this is silly right now
    # but would like to add logic for the composition of multiple deltas
    def __init__(self, removed_nodes, added_nodes, removed_edges, added_edges):
        self.removed_nodes = removed_nodes
        self.added_nodes = added_nodes
        self.removed_edges = removed_edges
        self.added_edges = added_edges


def combine_deltas(deltas):
    total_added_nodes = pd.concat(
        [delta.added_nodes for delta in deltas], verify_integrity=True
    )
    total_removed_nodes = pd.concat(
        [delta.removed_nodes for delta in deltas], verify_integrity=True
    )

    total_added_edges = pd.concat(
        [
            delta.added_edges.set_index(["source", "target"], drop=True)
            for delta in deltas
        ],
        verify_integrity=True,
    ).reset_index(drop=False)
    total_removed_edges = pd.concat(
        [
            delta.removed_edges.set_index(["source", "target"], drop=True)
            for delta in deltas
        ],
        verify_integrity=True,
    ).reset_index(drop=False)

    return NetworkDelta(
        total_removed_nodes, total_added_nodes, total_removed_edges, total_added_edges
    )


def get_network_changes(root_id, client, filtered=True):
    change_log = get_detailed_change_log(root_id, client, filtered=filtered)

    edit_lineage_graph = nx.DiGraph()
    networkdeltas_by_operation = {}
    for operation_id in tqdm(change_log.index):
        row = change_log.loc[operation_id]
        is_merge = row["is_merge"]
        before_root_ids = row["before_root_ids"]
        after_root_ids = row["roots"]

        # grabbing the union of before/after nodes/edges
        # NOTE: this is where all the compute time comes from
        all_before_nodes, all_before_edges = get_all_nodes_edges(
            before_root_ids, client, positions=False
        )
        all_after_nodes, all_after_edges = get_all_nodes_edges(
            after_root_ids, client, positions=False
        )

        # finding the nodes that were added or removed, simple set logic
        added_nodes_index = all_after_nodes.index.difference(all_before_nodes.index)
        added_nodes = all_after_nodes.loc[added_nodes_index]
        removed_nodes_index = all_before_nodes.index.difference(all_after_nodes.index)
        removed_nodes = all_before_nodes.loc[removed_nodes_index]

        # finding the edges that were added or removed, simple set logic again
        removed_edges, added_edges = get_changed_edges(
            all_before_edges, all_after_edges
        )

        # keep track of what changed
        networkdeltas_by_operation[operation_id] = NetworkDelta(
            removed_nodes, added_nodes, removed_edges, added_edges
        )

        # summarize in edit lineage for L2 level
        for node1 in removed_nodes.index:
            for node2 in added_nodes.index:
                edit_lineage_graph.add_edge(
                    node1, node2, operation_id=operation_id, is_merge=is_merge
                )

    return networkdeltas_by_operation, edit_lineage_graph


def get_network_metachanges(networkdeltas_by_operation, edit_lineage_graph):
    meta_operation_map = {}
    for i, component in enumerate(nx.weakly_connected_components(edit_lineage_graph)):
        subgraph = edit_lineage_graph.subgraph(component)
        subgraph_operations = set()
        for _, _, data in subgraph.edges(data=True):
            subgraph_operations.add(data["operation_id"])
        meta_operation_map[i] = subgraph_operations

    networkdeltas_by_meta_operation = {}
    for meta_operation_id, operation_ids in meta_operation_map.items():
        meta_networkdelta = combine_deltas(
            [networkdeltas_by_operation[operation_id] for operation_id in operation_ids]
        )
        networkdeltas_by_meta_operation[meta_operation_id] = meta_networkdelta

    return networkdeltas_by_meta_operation, meta_operation_map
