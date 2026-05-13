"""
Couchbase Lite storage backend — persists the graph in a CBL database.

Schema:
    Collection "nodes": one document per graph node
        doc ID = node_id (e.g. "func::src/utils/mailer.py::emails")
        body   = { type, name, path, line_start, ... , embedding? }

    Collection "edges": one document per graph edge
        doc ID = "{source}--{type}-->{target}" (deterministic)
        body   = { source, target, type }
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from .ctypes_api import CBL

if TYPE_CHECKING:
    from apollo.graph.incremental import GraphDiff


class CouchbaseLiteStore:
    """Persist a NetworkX graph to a Couchbase Lite database."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._cbl: CBL | None = None

    def _open(self) -> CBL:
        if self._cbl is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._cbl = CBL(self._db_path)
        return self._cbl

    def save(self, graph: nx.DiGraph) -> None:
        """Persist the full graph (full rebuild, not incremental)."""
        cbl = self._open()
        nodes_col = cbl.get_or_create_collection("nodes")
        edges_col = cbl.get_or_create_collection("edges")

        # Clear existing data
        self._purge_all(cbl, nodes_col, "nodes")
        self._purge_all(cbl, edges_col, "edges")

        # Bulk insert nodes
        embedding_dim = 0
        cbl.begin_transaction()
        try:
            for node_id, attrs in graph.nodes(data=True):
                doc = dict(attrs)
                emb = doc.get("embedding")
                if emb and not embedding_dim:
                    embedding_dim = len(emb)
                cbl.save_document_json(nodes_col, node_id, json.dumps(doc, default=str))

            # Bulk insert edges
            for src, dst, attrs in graph.edges(data=True):
                etype = attrs.get("type", "")
                edge_id = f"{src}--{etype}-->{dst}"
                doc = {"source": src, "target": dst, **attrs}
                cbl.save_document_json(edges_col, edge_id, json.dumps(doc, default=str))

            cbl.end_transaction(commit=True)
        except Exception:
            cbl.end_transaction(commit=False)
            raise

        # Create indexes
        self._create_indexes(cbl, nodes_col, edges_col, embedding_dim)

    def load(self, *, include_embeddings: bool = True) -> nx.DiGraph:
        """Load the graph from CBL into a NetworkX DiGraph."""
        cbl = self._open()
        graph = nx.DiGraph()

        # Load nodes
        rows = cbl.execute_query("SELECT META().id AS _id, * FROM nodes")
        for row in rows:
            node_id = row.get("_id")
            if not node_id:
                continue
            attrs = row.get("nodes", row)
            if isinstance(attrs, str):
                attrs = json.loads(attrs)
            attrs = dict(attrs)
            attrs.pop("_id", None)
            if not include_embeddings:
                attrs.pop("embedding", None)
            graph.add_node(node_id, **attrs)

        # Load edges
        rows = cbl.execute_query("SELECT META().id AS _id, * FROM edges")
        for row in rows:
            edge_data = row.get("edges", row)
            if isinstance(edge_data, str):
                edge_data = json.loads(edge_data)
            edge_data = dict(edge_data)
            src = edge_data.pop("source", None)
            dst = edge_data.pop("target", None)
            if src and dst:
                graph.add_edge(src, dst, **edge_data)

        return graph

    def save_diff(self, diff: GraphDiff, graph: nx.DiGraph | None = None) -> None:
        """Save only the changes (diff) to the database.
        
        Performs targeted document upserts/purges within a single transaction.
        This is much more efficient than save() for large graphs with small changes.
        
        Args:
            diff: GraphDiff containing nodes/edges to add/remove/modify
            graph: Optional graph to read new node/edge attributes from
                   If not provided, only purges are performed
        """
        cbl = self._open()
        nodes_col = cbl.get_or_create_collection("nodes")
        edges_col = cbl.get_or_create_collection("edges")
        
        cbl.begin_transaction()
        try:
            # Remove deleted nodes
            for node_id in diff.nodes_removed:
                cbl.purge_document(nodes_col, node_id)
            
            # Update/add nodes (modified + added)
            if graph is not None:
                for node_id in diff.nodes_added + diff.nodes_modified:
                    if node_id in graph.nodes:
                        attrs = dict(graph.nodes[node_id])
                        cbl.save_document_json(nodes_col, node_id, json.dumps(attrs, default=str))
            
            # Remove deleted edges
            for src, etype, dst in diff.edges_removed:
                edge_id = f"{src}--{etype}-->{dst}"
                cbl.purge_document(edges_col, edge_id)
            
            # Add new edges
            if graph is not None:
                for src, etype, dst in diff.edges_added:
                    edge_id = f"{src}--{etype}-->{dst}"
                    if graph.has_edge(src, dst):
                        attrs = dict(graph.edges[src, dst])
                        doc = {"source": src, "target": dst, **attrs}
                        cbl.save_document_json(edges_col, edge_id, json.dumps(doc, default=str))
            
            cbl.end_transaction(commit=True)
        except Exception:
            cbl.end_transaction(commit=False)
            raise

    def close(self) -> None:
        if self._cbl is not None:
            self._cbl.close()
            self._cbl = None

    def delete(self) -> None:
        """Close the database and remove the database directory."""
        self.close()
        db_dir = Path(self._db_path)
        if db_dir.exists():
            shutil.rmtree(db_dir)

    # -- Internal helpers ---

    def _purge_all(self, cbl: CBL, collection, collection_name: str) -> None:
        """Remove all documents from a collection."""
        rows = cbl.execute_query(f"SELECT META().id AS _id FROM {collection_name}")
        if not rows:
            return
        cbl.begin_transaction()
        try:
            for row in rows:
                doc_id = row.get("_id")
                if doc_id:
                    cbl.purge_document(collection, doc_id)
            cbl.end_transaction(commit=True)
        except Exception:
            cbl.end_transaction(commit=False)
            raise

    def _create_indexes(
        self, cbl: CBL, nodes_col, edges_col, embedding_dim: int
    ) -> None:
        """Create value indexes (and vector index if EE available)."""
        cbl.create_value_index(nodes_col, "idx_type", "type")
        cbl.create_value_index(nodes_col, "idx_name", "name")
        cbl.create_value_index(nodes_col, "idx_path", "path")

        cbl.create_value_index(edges_col, "idx_source", "source")
        cbl.create_value_index(edges_col, "idx_target", "target")
        cbl.create_value_index(edges_col, "idx_edge_type", "type")

        if embedding_dim > 0:
            centroids = max(1, int(math.sqrt(cbl.collection_count(nodes_col))))
            created = cbl.create_vector_index(
                nodes_col,
                "idx_embedding",
                "embedding",
                dimensions=embedding_dim,
                centroids=centroids,
            )
            if not created:
                pass  # Community Edition — vector search via brute-force SQL++

    # -- Async multi-get -----------------------------------------------

    async def aget_node_docs(
        self, node_ids: list[str]
    ) -> dict[str, dict | None]:
        """Fetch many node documents from CBL **in parallel**.

        Wraps :meth:`CBL.aget_documents_json` (which uses
        ``asyncio.to_thread`` + ``asyncio.gather``) and JSON-decodes
        the results. Returns ``{ node_id: attrs|None }`` — missing
        documents map to ``None``. Order of input IDs is preserved.

        Use this instead of a serial ``for nid in ids:
        cbl.get_document_json(nid)`` loop whenever a request handler
        needs multiple node payloads — Couchbase Lite queries are
        single-threaded, so off-loading each get to the thread pool
        keeps the FastAPI event loop responsive.
        """
        cbl = self._open()
        nodes_col = cbl.get_or_create_collection("nodes")
        raw = await cbl.aget_documents_json(nodes_col, list(node_ids))
        out: dict[str, dict | None] = {}
        for nid, body in zip(node_ids, raw):
            if body is None:
                out[nid] = None
                continue
            try:
                attrs = json.loads(body)
                if isinstance(attrs, str):
                    attrs = json.loads(attrs)
                attrs.pop("_id", None)
                out[nid] = attrs
            except Exception:
                out[nid] = None
        return out

    # -- Expose CBL for search backends ---

    @property
    def cbl(self) -> CBL:
        return self._open()
