"""Workspaces: named, restorable snapshots of the Explore view.

Plain CRUD over the ``workspaces`` table — no Earth Engine, no jobs. The
persisted ``state`` is a versioned pydantic blob (:class:`WorkspaceState`);
storing it as validated JSON means an unknown schema version is rejected on
the way in, and a shape change in a later phase is an explicit migration of
that model, never a guess at load time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from openearth_api.deps import get_db_engine
from openearth_api.models import Workspace, utcnow_iso
from openearth_api.schemas import WorkspaceIn, WorkspaceOut, WorkspaceState

if TYPE_CHECKING:
    from sqlalchemy import Engine

router = APIRouter(tags=["workspaces"])

EngineDep = Annotated["Engine", Depends(get_db_engine)]

_DUPLICATE = "already exists."


def _to_out(row: Workspace) -> WorkspaceOut:
    assert row.id is not None  # persisted rows always have an id
    return WorkspaceOut(
        id=row.id,
        name=row.name,
        state=WorkspaceState.model_validate_json(row.state_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/workspaces")
def list_workspaces(engine: EngineDep) -> list[WorkspaceOut]:
    with Session(engine) as session:
        rows = session.exec(select(Workspace).order_by(Workspace.name)).all()
        return [_to_out(row) for row in rows]


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceIn, engine: EngineDep) -> WorkspaceOut:
    now = utcnow_iso()
    row = Workspace(
        name=body.name,
        state_json=body.state.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"Workspace {body.name!r} {_DUPLICATE}"
            ) from exc
        session.refresh(row)
        return _to_out(row)


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: int, engine: EngineDep) -> WorkspaceOut:
    with Session(engine) as session:
        row = session.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No workspace {workspace_id}.")
        return _to_out(row)


@router.put("/workspaces/{workspace_id}")
def update_workspace(workspace_id: int, body: WorkspaceIn, engine: EngineDep) -> WorkspaceOut:
    """Replace a workspace's name and state (the header 'Update' action);
    ``created_at`` is preserved. Renaming onto another workspace's name → 409."""
    with Session(engine) as session:
        row = session.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No workspace {workspace_id}.")
        row.name = body.name
        row.state_json = body.state.model_dump_json()
        row.updated_at = utcnow_iso()
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"Workspace {body.name!r} {_DUPLICATE}"
            ) from exc
        session.refresh(row)
        return _to_out(row)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(workspace_id: int, engine: EngineDep) -> Response:
    with Session(engine) as session:
        row = session.get(Workspace, workspace_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No workspace {workspace_id}.")
        session.delete(row)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
