# %%

import caveclient as cc

client = cc.CAVEclient("minnie65_phase3_v1")

root_id = 864691135731235769

change_log = client.chunkedgraph.get_change_log(root_id)

details = client.chunkedgraph.get_operation_details(change_log["operations_ids"])
detail = next(iter(details.items()))[1]

# trying to check if these nodes referenced in "added_edges" are valid
pre_id, post_id = detail["added_edges"][0]

# client.chunkedgraph.is_valid_nodes([pre_id, post_id])

# client.chunkedgraph.is_valid_nodes([1234])

import numpy as np
import datetime

code = datetime.datetime.utcnow()

# %%
print([pre_id, post_id])
print(client.chunkedgraph.is_valid_nodes([pre_id, post_id]))
assert np.array([True, True]).all()
# %%

query_nodes = [0, -1]
out = client.chunkedgraph.is_valid_nodes(query_nodes)
print(out)
assert not np.any(out)
