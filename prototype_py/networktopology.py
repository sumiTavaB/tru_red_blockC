import networkx as nx
import matplotlib.pyplot as plt


def draw_topology(nodes, adjacency, authority, verifiers):
    """
    Draws an undirected, acyclic network with:
      - nodes: list of node labels (uppercase strings)
      - adjacency: dict mapping each node to its neighbor list
      - authority: label of the authority node
      - verifiers: list of verifier node labels
    """
    G = nx.Graph()
    # Add all nodes
    G.add_nodes_from(nodes)
    # Add edges (undirected) from adjacency
    for u, nbrs in adjacency.items():
        u = u.strip().upper()
        for v in nbrs:
            v = v.strip().upper()
            if v in nodes:
                G.add_edge(u, v)

    # Layout
    pos = nx.spring_layout(G)
    plt.figure(figsize=(8, 8))

    # Draw edges
    nx.draw_networkx_edges(G, pos, edge_color='gray', alpha=0.7)

    # Public nodes (neither authority nor verifier)
    public_nodes = [n for n in nodes if n not in verifiers and n != authority]
    nx.draw_networkx_nodes(
        G, pos,
        nodelist=public_nodes,
        node_color='lightgray',
        node_size=500,
        label='Publics'
    )

    # Verifiers in shades of blue
    if verifiers:
        cmap = plt.get_cmap('Blues')
        for idx, v in enumerate(verifiers):
            color = cmap(0.3 + 0.7 * (idx / max(1, len(verifiers)-1)))
            nx.draw_networkx_nodes(
                G, pos,
                nodelist=[v],
                node_color=[color],
                node_size=700,
                label=f'Verifier {v}'
            )

    # Authority in red
    nx.draw_networkx_nodes(
        G, pos,
        nodelist=[authority],
        node_color='red',
        node_size=900,
        label=f'Authority {authority}'
    )

    # Labels
    nx.draw_networkx_labels(G, pos, font_size=10)
    plt.title("Network Topology")
    plt.axis('off')
    plt.show()


# If run standalone, allow quick test
if __name__ == '__main__':
    nodes = ['A', 'B', 'C', 'D', 'E']
    adjacency = {'A': ['B', 'C'], 'B': ['A', 'D'], 'C': ['A'], 'D': ['B', 'E'], 'E': ['D']}
    authority = 'E'
    verifiers = ['C', 'D']
    draw_topology(nodes, adjacency, authority, verifiers)
