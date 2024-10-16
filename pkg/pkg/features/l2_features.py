from abc import abstractmethod
from typing import Optional, Union

import joblib
import numpy as np
import pandas as pd
from caveclient import CAVEclient
from networkframe import NetworkFrame
from numpy.typing import ArrayLike
from requests.exceptions import HTTPError

FEATURES = [
    "area_nm2",
    "max_dt_nm",
    "mean_dt_nm",
    "pca",
    "pca_val",
    "rep_coord_nm",
    "size_nm3",
]


# TODO make sure this is in the right ordering
# TODO figure out if there's a good solution for sign flips
def _unwrap_pca(pca):
    if np.isnan(pca).all():
        return np.full(9, np.nan)
    return np.array(pca).ravel()


def _unwrap_pca_val(pca):
    if np.isnan(pca).all():
        return np.full(3, np.nan)

    return np.array(pca).ravel()


# def rewrap_pca(pca):
#     # take the vector and transform back into 3x3 matrix
#     if np.isnan(pca).all():
#         return np.full((3, 3), np.nan)
#     return np.abs(np.array(pca).reshape(3, 3))


def process_node_data(node_data):
    if node_data.empty:
        return None
    if node_data.isnull().all().all():
        return None
    if not np.isin(
        ["area_nm2", "max_dt_nm", "mean_dt_nm", "size_nm3"], node_data.columns
    ).all():
        scalar_features = pd.DataFrame(
            index=node_data.index,
            columns=["area_nm2", "max_dt_nm", "mean_dt_nm", "size_nm3"],
        )
    else:
        scalar_features = node_data[
            ["area_nm2", "max_dt_nm", "mean_dt_nm", "size_nm3"]
        ].astype(float)

    if "pca" in node_data.columns:
        pca_unwrapped = np.stack(node_data["pca"].apply(_unwrap_pca).values)
        pca_unwrapped = pd.DataFrame(
            pca_unwrapped,
            columns=[f"pca_unwrapped_{i}" for i in range(9)],
            index=node_data.index,
        )
    else:
        pca_unwrapped = pd.DataFrame(
            index=node_data.index,
            columns=[f"pca_unwrapped_{i}" for i in range(9)],
        )

    if "pca_val" not in node_data.columns:
        pca_val_unwrapped = pd.DataFrame(
            index=node_data.index,
            columns=[f"pca_val_unwrapped_{i}" for i in range(3)] + ["pca_ratio_01"],
        )
    else:
        pca_val_unwrapped = np.stack(node_data["pca_val"].apply(_unwrap_pca_val).values)
        pca_val_unwrapped = pd.DataFrame(
            pca_val_unwrapped,
            columns=[f"pca_val_unwrapped_{i}" for i in range(3)],
            index=node_data.index,
        )
        pca_val_unwrapped["pca_ratio_01"] = (
            pca_val_unwrapped["pca_val_unwrapped_0"]
            / pca_val_unwrapped["pca_val_unwrapped_1"]
        )

    if "rep_coord_nm" not in node_data.columns:
        rep_coord_unwrapped = pd.DataFrame(
            index=node_data.index,
            columns=["rep_coord_x", "rep_coord_y", "rep_coord_z"],
        )
    else:
        rep_coord_unwrapped = np.stack(node_data["rep_coord_nm"].values)
        rep_coord_unwrapped = pd.DataFrame(
            rep_coord_unwrapped,
            columns=["rep_coord_x", "rep_coord_y", "rep_coord_z"],
            index=node_data.index,
        )

    clean_node_data = pd.concat(
        [scalar_features, pca_unwrapped, pca_val_unwrapped, rep_coord_unwrapped], axis=1
    )

    return clean_node_data


class BaseWrangler:
    def __init__(
        self,
        client: CAVEclient,
        n_jobs: int = 1,
        continue_on_error: bool = True,
        verbose: Union[int, bool] = False,
    ):
        self.client = client
        self.n_jobs = n_jobs
        self.continue_on_error = continue_on_error
        self.verbose = verbose

    def print(self, msg: str, level: int = 0) -> None:
        if self.verbose >= level and self.n_jobs == 1:
            print(msg)

    def get_features(
        self,
        object_ids: ArrayLike,
        bounds_by_object: Optional[ArrayLike] = None,
        points_by_object: Optional[ArrayLike] = None,
    ) -> pd.DataFrame:
        """Extract features for a list of objects.

        Parameters
        ----------
        object_ids
            Array-like of object ids to extract features for. These could be root IDs,
            but can also be any other node ID from the CAVE chunkedgraph.
        bounds_by_object
            List of bounds to extract features for. If None, no bounds are used in the
            extraction of features for the specified object ID. Some feature extractors
            may not support bounds or may require them.

        Returns
        -------
        :
            DataFrame containing extracted features for the specified objects. The outer
            index is the object ID; the inner index may be some other ID for children
            of the object ID.
        """
        if isinstance(object_ids, (int, np.integer)):
            object_ids = [object_ids]

        if self.n_jobs == 1:
            data_by_object = []
            for i, object_id in enumerate(object_ids):
                if bounds_by_object is not None:
                    bounds = bounds_by_object[i]
                else:
                    bounds = None
                object_node_data = self._extract_for_object(object_id, bounds)
                data_by_object.append(object_node_data)
        else:
            data_by_object = joblib.Parallel(n_jobs=self.n_jobs, verbose=self.verbose)(
                joblib.delayed(self._extract_for_object)(
                    object_id,
                    bounds_by_object[i] if bounds_by_object is not None else None,
                )
                for i, object_id in enumerate(object_ids)
            )

        data = self._combine_features(data_by_object)
        return data

    @abstractmethod
    def _extract_for_object(self, object_id, bounds):
        pass

    @abstractmethod
    def _combine_features(self, data_by_object):
        pass


class L2AggregateWrangler(BaseWrangler):
    def __init__(
        self,
        client: CAVEclient,
        n_jobs=1,
        continue_on_error=True,
        verbose=False,
        neighborhood_hops=5,
        drop_self_in_neighborhood=True,
    ):
        """
        Feature extractor for level 2 nodes, using graph aggregation to average features
        for neighboring nodes in the level 2 graph.

        Parameters
        ----------
        client
            CAVEclient instance.
        n_jobs
            Number of parallel jobs to use for feature extraction. Default is 1 for no
            parallelization.
        continue_on_error
            If True, continue extracting features for other objects if an error occurs
            for one object.
        verbose
            If True, print progress messages. Higher integer values will indicate more
            verbosity.
        neighborhood_hops
            Number of hops to consider for neighborhood aggregation.
        drop_self_in_neighborhood
            If True, do not include the node itself in the neighborhood aggregation.
            A separate vector of features is still computed for the node itself.
        """
        super().__init__(
            client=client,
            n_jobs=n_jobs,
            continue_on_error=continue_on_error,
            verbose=verbose,
        )
        self.neighborhood_hops = neighborhood_hops
        self.drop_self_in_neighborhood = drop_self_in_neighborhood

    def _combine_features(self, data_by_object):
        if all([x is None for x in data_by_object]):
            raise NotImplementedError("need a fix for this case, pandas mad")
        node_data = pd.concat(data_by_object)
        node_data.reset_index(inplace=True)
        node_data.set_index(["object_id", "l2_id"], inplace=True)
        return node_data

    def _extract_for_object(self, object_id, bounds):
        self.print(f"Extracting features for object {object_id}", level=2)
        object_node_data = self._extract_node_features(object_id, bounds)
        if object_node_data is None:
            return None

        self.print(f"Extracting level 2 graph for object {object_id}", level=2)
        object_edges = self._extract_edges(object_id, bounds)

        self.print(f"Extracting neighborhood features for object {object_id}", level=2)
        object_neighborhood_features = self._compute_neighborhood_features(
            object_node_data, object_edges
        )

        object_node_data = object_node_data.join(object_neighborhood_features)

        object_node_data["object_id"] = object_id
        return object_node_data

    def _extract_node_features(self, object_id, bounds):
        l2_ids = self.client.chunkedgraph.get_leaves(
            object_id, stop_layer=2, bounds=bounds
        )
        try:
            node_data = pd.DataFrame(
                self.client.l2cache.get_l2data(l2_ids, attributes=FEATURES)
            ).T
            node_data.index = node_data.index.astype(int)
            node_data.index.name = "l2_id"
            node_data = process_node_data(node_data)
            return node_data
        except HTTPError as e:
            if self.continue_on_error:
                self.print(f"Error fetching data for object {object_id}: {e}", level=2)
                return None
            else:
                raise e

    def _extract_edges(self, object_id, bounds):
        edges = self.client.chunkedgraph.level2_chunk_graph(object_id, bounds=bounds)
        edges = pd.DataFrame(edges, columns=["source", "target"])
        return edges

    def _compute_neighborhood_features(
        self, object_node_data: pd.DataFrame, object_edges: pd.DataFrame
    ):
        nf = NetworkFrame(object_node_data, object_edges)
        k = self.neighborhood_hops
        distance = None
        if distance is not None:
            raise NotImplementedError(
                "Distance-based neighborhood features not yet implemented"
            )
        else:
            assert k is not None
            neighborhoods = nf.k_hop_decomposition(k=k, directed=False)

        rows = []
        for node, neighborhood in neighborhoods.items():
            neighborhood_nodes = neighborhood.nodes
            if self.drop_self_in_neighborhood:
                neighborhood_nodes = neighborhood_nodes.drop(node)
            # TODO could be generalized to other permutation invariant aggregations
            agg_neighbor_features = neighborhood_nodes.mean(skipna=True).to_frame().T
            rows.append(agg_neighbor_features)
        neighborhood_features = pd.concat(rows)
        neighborhood_features.rename(
            columns=lambda x: f"{x}_neighbor_agg", inplace=True
        )
        neighborhood_features.index = list(neighborhoods.keys())
        neighborhood_features.index.name = "l2_id"
        return neighborhood_features
