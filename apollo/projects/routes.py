"""FastAPI routes for project management (/api/projects/*)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union, Literal
import os
import fnmatch

from fastapi import FastAPI, HTTPException, Request

from .annotations import AnnotationManager


def _get_annotation_manager(project_manager) -> AnnotationManager:
    """Build an AnnotationManager bound to the currently open project."""
    if not project_manager.manifest or not project_manager.root_dir:
        raise HTTPException(status_code=400, detail="No project currently open")
    return AnnotationManager(
        project_root=project_manager.root_dir,
        project_id=project_manager.manifest.project_id,
    )


def register_project_routes(app: FastAPI, project_manager, store, backend: str):
    """Register all /api/projects/* endpoints."""
    
    # ────────────────────────────────────────────────────────────────
    # POST /api/projects/open
    # ────────────────────────────────────────────────────────────────
    @app.post("/api/projects/open")
    async def open_project(request: Request):
        """Open a project folder (bootstrap or normal).
        
        - If apollo.json exists and initial_index_completed=true, 
          return ProjectInfo with needs_bootstrap=false.
        - If apollo.json doesn't exist or initial_index_completed=false,
          return ProjectInfo with needs_bootstrap=true.
        """
        try:
            body = await request.json()
            path = body.get("path")
            
            if not path:
                raise HTTPException(status_code=400, detail="Missing path")
            
            path = Path(path).resolve()
            if not path.is_dir():
                raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
            
            # Check for nested projects
            parent = path.parent
            while parent != parent.parent:  # Stop at filesystem root
                apollo_dir = parent / "_apollo"
                if apollo_dir.exists() and (apollo_dir / "apollo.json").exists():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot open nested project. Parent project at {parent}",
                    )
                parent = parent.parent
            
            # Open via project manager
            project_info = project_manager.open(str(path))
            return project_info.to_dict()
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # ────────────────────────────────────────────────────────────────
    # POST /api/projects/init
    # ────────────────────────────────────────────────────────────────
    @app.post("/api/projects/init")
    async def init_project(request: Request):
        """Initialize a new project with custom filters.
        
        Triggered when user submits the bootstrap wizard with filters.
        This enqueues an indexing job (Phase 8).
        """
        try:
            body = await request.json()
            path = body.get("path")
            filters = body.get("filters")
            
            if not path:
                raise HTTPException(status_code=400, detail="Missing path")
            
            path = Path(path).resolve()
            
            # Initialize with filters
            project_info = project_manager.init(str(path), filters)
            
            # TODO: Enqueue indexing job (Phase 8 integration)
            # For now, just return the ProjectInfo
            
            return project_info.to_dict()
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # ────────────────────────────────────────────────────────────────
    # PUT /api/projects/filters
    # ────────────────────────────────────────────────────────────────
    @app.put("/api/projects/filters")
    async def update_project_filters(request: Request):
        """Update filters for the current project."""
        try:
            body = await request.json()
            filters = body.get("filters")
            
            if not filters:
                raise HTTPException(status_code=400, detail="Missing filters")
            
            project_info = project_manager.update_filters(filters)
            return project_info.to_dict()
            
        except HTTPException:
            raise
        except RuntimeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # ────────────────────────────────────────────────────────────────
    # POST /api/projects/reprocess
    # ────────────────────────────────────────────────────────────────
    @app.post("/api/projects/reprocess")
    async def reprocess_project(request: Request):
        """Reprocess the current project.
        
        mode: "incremental" | "full"
        - incremental: Re-index changed files only
        - full: Delete graph/embeddings, rebuild from scratch
        
        TODO: This should enqueue an indexing job (Phase 8).
        """
        try:
            body = await request.json()
            mode = body.get("mode", "incremental")
            
            if mode not in ["incremental", "full"]:
                raise HTTPException(
                    status_code=400,
                    detail='mode must be "incremental" or "full"',
                )
            
            current = project_manager.current_info()
            if not current:
                raise HTTPException(
                    status_code=400,
                    detail="No project currently open",
                )
            
            # TODO: Enqueue the reindexing job here.
            # For now, return a placeholder response.
            return {
                "status": "queued",
                "mode": mode,
                "project_id": current.project_id,
                # In Phase 8, this would be a proper IndexJob response
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # ────────────────────────────────────────────────────────────────
    # POST /api/projects/leave
    # ────────────────────────────────────────────────────────────────
    @app.post("/api/projects/leave")
    async def leave_project(request: Request):
        """Remove the current project from Apollo.
        
        Deletes _apollo/ and _apollo_web/. Requires confirm=true.
        """
        try:
            body = await request.json()
            confirm = body.get("confirm", False)
            
            if not confirm:
                raise HTTPException(
                    status_code=400,
                    detail="Confirmation required (confirm=true)",
                )
            
            deleted = project_manager.leave()
            return {"status": "removed", "deleted": deleted}
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # ────────────────────────────────────────────────────────────────
    # GET /api/projects/current
    # ────────────────────────────────────────────────────────────────
    @app.get("/api/projects/current")
    def get_current_project():
        """Get info about the currently open project."""
        current = project_manager.current_info()
        if not current:
            return None
        return current.to_dict()
    
    # ────────────────────────────────────────────────────────────────
    # GET /api/projects/tree
    # ────────────────────────────────────────────────────────────────
    @app.get("/api/projects/tree")
    def get_project_tree(depth: int = 3):
        """Get folder tree for the current project (for wizard).
        
        Returns a hierarchical structure with file/dir counts at each level.
        """
        current = project_manager.current_info()
        if not current:
            raise HTTPException(status_code=400, detail="No project currently open")
        
        root = Path(current.root_dir)
        
        def build_tree(node_path: Path, current_depth: int) -> dict:
            """Recursively build tree structure."""
            try:
                is_dir = node_path.is_dir()
                is_file = node_path.is_file()
                
                if not (is_dir or is_file):
                    return None
                
                rel_path = node_path.relative_to(root)
                name = node_path.name or str(rel_path)
                
                result = {
                    "name": name,
                    "path": str(rel_path) if rel_path != Path(".") else ".",
                    "type": "dir" if is_dir else "file",
                }
                
                # For directories, count children and recurse if depth allows
                if is_dir:
                    try:
                        children = list(node_path.iterdir())
                    except (PermissionError, OSError):
                        children = []
                    
                    dirs = [c for c in children if c.is_dir()]
                    files = [c for c in children if c.is_file()]
                    
                    result["child_dir_count"] = len(dirs)
                    result["child_file_count"] = len(files)
                    
                    # Recurse if depth allows
                    if current_depth < depth:
                        result["children"] = []
                        for child in sorted(children):
                            try:
                                child_tree = build_tree(child, current_depth + 1)
                                if child_tree:
                                    result["children"].append(child_tree)
                            except Exception:
                                pass
                    else:
                        result["children"] = []
                
                return result
            
            except Exception:
                return None
        
        tree = build_tree(root, 0)
        if not tree:
            raise HTTPException(status_code=500, detail="Failed to build tree")
        
        return tree

    # ────────────────────────────────────────────────────────────────
    # Annotation endpoints (Phase 11)
    # ────────────────────────────────────────────────────────────────

    @app.post("/api/annotations/create")
    async def create_annotation(request: Request):
        """Create a new annotation.

        Body: {
            type: "highlight"|"bookmark"|"note"|"tag",
            target: {type:"file"|"node", file_path|node_id: str},
            content?: str,
            tags?: list[str],
            color?: str,
            highlight_range?: {start_line, end_line, start_col?, end_col?}
        }
        """
        try:
            body = await request.json()
            mgr = _get_annotation_manager(project_manager)
            ann = mgr.create(
                type=body.get("type"),
                target=body.get("target") or {},
                content=body.get("content"),
                tags=body.get("tags"),
                color=body.get("color"),
                highlight_range=body.get("highlight_range"),
            )
            return ann.to_dict()
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/annotations/by-target")
    def annotations_by_target(file: Optional[str] = None, node: Optional[str] = None):
        """Find annotations for a file path or graph node ID."""
        if not file and not node:
            raise HTTPException(status_code=400, detail="Provide ?file= or ?node=")
        mgr = _get_annotation_manager(project_manager)
        if file:
            results = mgr.find_by_target_file(file)
        else:
            results = mgr.find_by_target_node(node)
        return {"annotations": [a.to_dict() for a in results]}

    @app.get("/api/annotations/by-tag")
    def annotations_by_tag(tag: str):
        """Find annotations carrying the given tag."""
        if not tag:
            raise HTTPException(status_code=400, detail="Missing tag")
        mgr = _get_annotation_manager(project_manager)
        return {"annotations": [a.to_dict() for a in mgr.find_by_tag(tag)]}

    @app.get("/api/annotations/collections")
    def list_annotation_collections():
        mgr = _get_annotation_manager(project_manager)
        return {"collections": [c.to_dict() for c in mgr.list_collections()]}

    @app.post("/api/annotations/collections")
    async def create_annotation_collection(request: Request):
        try:
            body = await request.json()
            name = body.get("name")
            if not name:
                raise HTTPException(status_code=400, detail="Missing name")
            mgr = _get_annotation_manager(project_manager)
            coll = mgr.create_collection(
                name=name,
                annotation_ids=body.get("annotation_ids"),
                description=body.get("description"),
            )
            return coll.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/annotations/collections/{collection_id}")
    def delete_annotation_collection(collection_id: str):
        mgr = _get_annotation_manager(project_manager)
        ok = mgr.delete_collection(collection_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Collection not found")
        return {"deleted": collection_id}

    @app.get("/api/annotations/{annotation_id}")
    def get_annotation(annotation_id: str):
        mgr = _get_annotation_manager(project_manager)
        ann = mgr.get(annotation_id)
        if not ann:
            raise HTTPException(status_code=404, detail="Annotation not found")
        return ann.to_dict()

    @app.put("/api/annotations/{annotation_id}")
    async def update_annotation(annotation_id: str, request: Request):
        try:
            body = await request.json()
            mgr = _get_annotation_manager(project_manager)
            ann = mgr.update(annotation_id, **body)
            if not ann:
                raise HTTPException(status_code=404, detail="Annotation not found")
            return ann.to_dict()
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/annotations/{annotation_id}")
    def delete_annotation(annotation_id: str):
        mgr = _get_annotation_manager(project_manager)
        ok = mgr.delete(annotation_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Annotation not found")
        return {"deleted": annotation_id}
