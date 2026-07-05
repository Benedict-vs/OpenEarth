"""Saved AOIs: name a region of interest and recall it later.

Plain CRUD over the ``aois`` table — no Earth Engine, no jobs. The name is
UNIQUE at the DB level, so a duplicate save fails the insert and is reported
as 409 rather than quietly shadowing the existing row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from openearth_api.deps import get_db_engine
from openearth_api.models import Aoi, utcnow_iso
from openearth_api.schemas import ROI_ADAPTER, AoiIn, AoiOut

if TYPE_CHECKING:
    from sqlalchemy import Engine

router = APIRouter(tags=["aois"])

EngineDep = Annotated["Engine", Depends(get_db_engine)]


def _to_out(row: Aoi) -> AoiOut:
    assert row.id is not None  # persisted rows always have an id
    return AoiOut(
        id=row.id,
        name=row.name,
        roi=ROI_ADAPTER.validate_json(row.roi_json),
        created_at=row.created_at,
    )


@router.get("/aois")
def list_aois(engine: EngineDep) -> list[AoiOut]:
    with Session(engine) as session:
        rows = session.exec(select(Aoi).order_by(Aoi.name)).all()
        return [_to_out(row) for row in rows]


@router.post("/aois", status_code=status.HTTP_201_CREATED)
def create_aoi(body: AoiIn, engine: EngineDep) -> AoiOut:
    body.roi.to_domain()  # geometry sanity check → InvalidROIError → 422
    row = Aoi(
        name=body.name,
        roi_json=ROI_ADAPTER.dump_json(body.roi).decode(),
        created_at=utcnow_iso(),
    )
    with Session(engine) as session:
        session.add(row)
        try:
            session.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail=f"AOI {body.name!r} already exists."
            ) from exc
        session.refresh(row)
        return _to_out(row)


@router.delete("/aois/{aoi_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_aoi(aoi_id: int, engine: EngineDep) -> Response:
    with Session(engine) as session:
        row = session.get(Aoi, aoi_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No AOI {aoi_id}.")
        session.delete(row)
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
