"""Graph algorithms behind an interface; metrics code never imports networkx."""
from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np
import scipy.sparse as sp


class GraphAlgo(Protocol):
    def scc(self, nodes: Sequence[str], edges: Sequence[tuple[str, str]]) -> list[list[str]]: ...
    def reachability(self, nodes: Sequence[str], edges: Sequence[tuple[str, str]]) -> sp.csr_matrix:
        """Boolean transitive closure including the diagonal (a node reaches itself)."""
    def order_by_clusters(self, nodes: Sequence[str], edges: Sequence[tuple[str, str, float]]) -> list[str]: ...


class NetworkxAlgo:
    def scc(self, nodes, edges):
        import networkx as nx
        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        g.add_edges_from(edges)
        return [sorted(c) for c in nx.strongly_connected_components(g)]

    def reachability(self, nodes, edges):
        n = len(nodes)
        idx = {x: i for i, x in enumerate(nodes)}
        if n == 0:
            return sp.csr_matrix((0, 0), dtype=bool)
        rows = [idx[a] for a, b in edges if a in idx and b in idx]
        cols = [idx[b] for a, b in edges if a in idx and b in idx]
        m = sp.coo_matrix((np.ones(len(rows), dtype=bool), (rows, cols)), shape=(n, n)).tocsr()
        m = (m + sp.identity(n, dtype=bool, format="csr")).astype(bool)
        # Repeated squaring over the boolean semiring: reach paths of length 2^k.
        while True:
            nxt = (m @ m).astype(bool)
            if nxt.nnz == m.nnz:
                return nxt.tocsr()
            m = nxt.tocsr()

    def order_by_clusters(self, nodes, edges):
        """Order nodes so tightly-connected clusters are contiguous (greedy modularity communities)."""
        import networkx as nx
        g = nx.Graph()
        g.add_nodes_from(nodes)
        for a, b, w in edges:
            if a != b:
                prev = g.get_edge_data(a, b, {}).get("weight", 0.0)
                g.add_edge(a, b, weight=prev + w)
        if g.number_of_edges() == 0:
            return sorted(nodes)
        comms = nx.community.greedy_modularity_communities(g, weight="weight")
        comms = sorted(comms, key=lambda c: (-len(c), sorted(c)[0]))
        order: list[str] = []
        for c in comms:
            order.extend(sorted(c))
        return order


def default_algo() -> GraphAlgo:
    return NetworkxAlgo()
