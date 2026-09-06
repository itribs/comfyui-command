export function getNode(app, nodeId) {
    const idStr = String(nodeId);

    if (idStr.includes(":")) {
        const parts = idStr.split(":").map(Number);
        let graph = app.graph;
        for (let i = 0; i < parts.length; i++) {
            const node = graph._nodes?.find((n) => n.id == parts[i]);
            if (!node) return null;
            if (i === parts.length - 1) return node;
            if (!node.subgraph) return null;
            graph = node.subgraph;
        }
        return null;
    }

    function findInGraph(graph) {
        for (const node of graph._nodes) {
            if (node.id == nodeId) return node;
            if (node.subgraph) {
                const found = findInGraph(node.subgraph);
                if (found) return found;
            }
        }
        return null;
    }
    return findInGraph(app.graph);
}