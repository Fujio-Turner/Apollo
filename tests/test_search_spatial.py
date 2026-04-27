"""Tests for search.spatial.SpatialSearch."""
import networkx as nx
import pytest

from search.spatial import SpatialSearch


def _add(graph, node_id, x, y, z=1.0, face=0, name=None, ntype="function", path="a.py"):
    graph.add_node(
        node_id,
        name=name or node_id,
        type=ntype,
        path=path,
        line_start=1,
        spatial={"x": float(x), "y": float(y), "z": float(z), "face": face},
    )


@pytest.fixture
def graph():
    g = nx.DiGraph()
    _add(g, "a", 0, 0, z=1.0)
    _add(g, "b", 1, 1, z=2.0)
    _add(g, "c", 5, 5, z=0.5)
    _add(g, "d", 10, 10, z=3.0, face=1)
    # Node intentionally without a "spatial" attribute
    g.add_node("no_spatial", name="no_spatial", type="function", path="x.py", line_start=1)
    g.add_edge("a", "b", type="calls")
    g.add_edge("b", "c", type="calls")
    g.add_edge("c", "d", type="imports")
    return g


class TestRangeQuery:
    def test_finds_nearby_nodes(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=0.0, cy=0.0, range_deg=2.0)
        ids = [r["id"] for r in out]
        assert "a" in ids
        assert "b" in ids
        assert "c" not in ids

    def test_filters_by_z_min(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=0.0, cy=0.0, range_deg=2.0, z_min=1.5)
        ids = [r["id"] for r in out]
        assert "b" in ids
        assert "a" not in ids

    def test_filters_by_face(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=10.0, cy=10.0, range_deg=2.0, face=1)
        assert {r["id"] for r in out} == {"d"}

        out_face0 = s.range_query(cx=10.0, cy=10.0, range_deg=2.0, face=0)
        assert out_face0 == []

    def test_skips_nodes_without_spatial(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=0.0, cy=0.0, range_deg=100.0)
        assert "no_spatial" not in [r["id"] for r in out]

    def test_results_sorted_by_distance(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=0.0, cy=0.0, range_deg=20.0)
        distances = [r["distance"] for r in out]
        assert distances == sorted(distances)

    def test_top_zero_returns_all(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=0.0, cy=0.0, range_deg=20.0, top=0)
        assert len(out) == 4

    def test_top_limits_results(self, graph):
        s = SpatialSearch(graph)
        out = s.range_query(cx=0.0, cy=0.0, range_deg=20.0, top=2)
        assert len(out) == 2


class TestFaceQuery:
    def test_returns_face_members(self, graph):
        s = SpatialSearch(graph)
        out = s.face_query(0)
        assert {r["id"] for r in out} == {"a", "b", "c"}

    def test_sorted_by_z_desc(self, graph):
        s = SpatialSearch(graph)
        out = s.face_query(0)
        zs = [r["spatial"]["z"] for r in out]
        assert zs == sorted(zs, reverse=True)

    def test_unknown_face_empty(self, graph):
        s = SpatialSearch(graph)
        assert s.face_query(99) == []


class TestNearNode:
    def test_excludes_self(self, graph):
        s = SpatialSearch(graph)
        out = s.near_node("a", range_deg=20.0, top=10)
        assert "a" not in [r["id"] for r in out]

    def test_returns_neighbours(self, graph):
        s = SpatialSearch(graph)
        out = s.near_node("a", range_deg=2.0, top=10)
        assert {r["id"] for r in out} == {"b"}

    def test_unknown_node(self, graph):
        s = SpatialSearch(graph)
        assert s.near_node("nope") == []

    def test_node_without_spatial(self, graph):
        s = SpatialSearch(graph)
        assert s.near_node("no_spatial") == []


class TestSpatialWalk:
    def test_walk_produces_rings(self, graph):
        s = SpatialSearch(graph)
        rings = s.spatial_walk("a", step=2.0, max_rings=3)
        assert len(rings) == 4  # ring 0..3
        # Ring 0 always contains the source node
        assert rings[0]["nodes"][0]["id"] == "a"

    def test_walk_unknown_node(self, graph):
        s = SpatialSearch(graph)
        assert s.spatial_walk("nope") == []

    def test_walk_no_spatial(self, graph):
        s = SpatialSearch(graph)
        assert s.spatial_walk("no_spatial") == []

    def test_walk_dedupes_across_rings(self, graph):
        s = SpatialSearch(graph)
        rings = s.spatial_walk("a", step=2.0, max_rings=5)
        seen_ids = []
        for r in rings:
            seen_ids.extend(n["id"] for n in r["nodes"])
        # No duplicate IDs across rings
        assert len(seen_ids) == len(set(seen_ids))


class TestCombinedSpatialStructural:
    def test_returns_matches_and_traversal(self, graph):
        s = SpatialSearch(graph)
        out = s.combined_spatial_structural(
            cx=0.0, cy=0.0, range_deg=2.0, direction="out", edge_type="calls", depth=1
        )
        assert "spatial_matches" in out
        assert "traversal_results" in out
        sources = [t["source"] for t in out["traversal_results"]]
        assert "a" in sources

    def test_traversal_respects_edge_type(self, graph):
        s = SpatialSearch(graph)
        out = s.combined_spatial_structural(
            cx=5.0, cy=5.0, range_deg=1.0, direction="out", edge_type="imports", depth=2
        )
        # c--imports-->d should appear
        c_traversal = next(t for t in out["traversal_results"] if t["source"] == "c")
        assert "d" in [n["id"] for n in c_traversal["neighbors"]]

    def test_traversal_inbound_direction(self, graph):
        s = SpatialSearch(graph)
        out = s.combined_spatial_structural(
            cx=5.0, cy=5.0, range_deg=1.0, direction="in", edge_type="calls", depth=1
        )
        # b--calls-->c, so 'in' from 'c' includes 'b'
        c_traversal = next(t for t in out["traversal_results"] if t["source"] == "c")
        assert "b" in [n["id"] for n in c_traversal["neighbors"]]
