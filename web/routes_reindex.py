"""FastAPI routes for reindex telemetry and control."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from apollo.projects.reindex import ReindexHistory, ReindexOrchestrator
from apollo.graph.reindex_config import ReindexConfig


def register_reindex_routes(app, project_manager) -> APIRouter:
    """Register reindex endpoints with FastAPI app.
    
    Requires project_manager to be available in app.state.
    
    Endpoints:
        GET /api/index/history - Reindex history (last 100 runs)
        GET /api/index/last - Most recent reindex stats
        GET /api/index/config - Current reindex configuration
        POST /api/index/config - Update reindex configuration
        GET /api/index/summary - Summary of reindex activity
    """
    router = APIRouter(prefix="/api/index", tags=["reindex"])
    
    def _get_orchestrator() -> ReindexOrchestrator:
        """Get reindex orchestrator for current project."""
        if not project_manager.manifest or not project_manager.root_dir:
            raise HTTPException(status_code=400, detail="No project is open")
        return ReindexOrchestrator(project_manager.root_dir)
    
    @router.get("/history")
    def get_reindex_history(limit: int = Query(20, ge=1, le=100)):
        """Get reindex history (last N runs, max 100)."""
        orchestrator = _get_orchestrator()
        history = orchestrator.history.load()
        
        # Return most recent `limit` runs
        recent = history[-limit:] if history else []
        return {
            "total_runs": len(history),
            "limit": limit,
            "runs": [h.to_dict() for h in recent],
        }
    
    @router.get("/last")
    def get_last_reindex():
        """Get most recent reindex statistics."""
        orchestrator = _get_orchestrator()
        stats = orchestrator.history.get_last()
        
        if not stats:
            return {"has_run": False, "stats": None}
        
        return {
            "has_run": True,
            "stats": stats.to_dict(),
        }
    
    @router.get("/summary")
    def get_reindex_summary():
        """Get summary of reindex activity."""
        orchestrator = _get_orchestrator()
        summary = orchestrator.history.get_summary()
        
        return {
            "configuration": orchestrator.config.to_dict(),
            "summary": summary,
        }
    
    @router.get("/config")
    def get_reindex_config():
        """Get current reindex configuration."""
        orchestrator = _get_orchestrator()
        return {
            "config": orchestrator.config.to_dict(),
            "effective_foreground_strategy": orchestrator.get_effective_strategy(is_foreground=True),
            "effective_background_strategy": orchestrator.get_effective_strategy(is_foreground=False),
        }
    
    @router.post("/config")
    def update_reindex_config(
        strategy: Optional[str] = None,
        sweep_interval_minutes: Optional[int] = None,
        sweep_on_session_start: Optional[bool] = None,
        local_max_hops: Optional[int] = None,
        force_full_after_runs: Optional[int] = None,
    ):
        """Update reindex configuration."""
        orchestrator = _get_orchestrator()
        
        # Build update dict from provided arguments
        updates = {}
        if strategy is not None:
            updates["strategy"] = strategy
        if sweep_interval_minutes is not None:
            updates["sweep_interval_minutes"] = sweep_interval_minutes
        if sweep_on_session_start is not None:
            updates["sweep_on_session_start"] = sweep_on_session_start
        if local_max_hops is not None:
            updates["local_max_hops"] = local_max_hops
        if force_full_after_runs is not None:
            updates["force_full_after_runs"] = force_full_after_runs
        
        if not updates:
            raise HTTPException(status_code=400, detail="No configuration parameters provided")
        
        # Create new config by merging
        current_dict = orchestrator.config.to_dict()
        current_dict.update(updates)
        
        try:
            new_config = ReindexConfig.from_dict(current_dict)
            new_config.validate()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        return {
            "updated": True,
            "config": new_config.to_dict(),
        }
    
    app.include_router(router)
    return router
