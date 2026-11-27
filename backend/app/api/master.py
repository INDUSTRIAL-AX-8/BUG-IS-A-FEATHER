"""마스터 관리 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.crud import make_crud_router, require_exists
from app.auth.deps import admin, viewer
from app.db import get_db
from app.core.spc.nelson import validate_config
from app.errors import AppError, not_found
from app.models import (
    Characteristic,
    ConditionDefinition,
    DefectType,
    Equipment,
    ItemType,
    ProcessStep,
    ProductDesign,
    ProductDesignComponent,
    ProductDesignStep,
    User,
)
from app.schemas.master import (
    CharacteristicIn,
    CharacteristicOut,
    ConditionDefinitionIn,
    ConditionDefinitionOut,
    DefectTypeIn,
    DefectTypeOut,
    EquipmentIn,
    EquipmentOut,
    ItemTypeIn,
    ItemTypeOut,
    ProcessStepIn,
    ProcessStepOut,
    ProductDesignIn,
    ProductDesignOut,
)
from app.services import audit


def _step_ref(db, data, _obj):
    require_exists(db, ProcessStep, data.get("process_step_id"), "공정 단계")


def _char_ref(db, data, _obj):
    require_exists(db, ProcessStep, data.get("process_step_id"), "공정 단계")
    require_exists(db, ItemType, data.get("item_type_id"), "부품 종류")
    if data.get("rule_config"):
        errors = validate_config(data["rule_config"])
        if errors:
            raise AppError(422, "넬슨룰 설정이 올바르지 않습니다.", errors)


routers = [
    make_crud_router(
        model=ItemType, schema_in=ItemTypeIn, schema_out=ItemTypeOut,
        prefix="/item-types", label="부품 종류", entity="item_type", order_by=ItemType.name,
    ),
    make_crud_router(
        model=ProcessStep, schema_in=ProcessStepIn, schema_out=ProcessStepOut,
        prefix="/process-steps", label="공정 단계", entity="process_step", order_by=ProcessStep.seq,
    ),
    make_crud_router(
        model=Equipment, schema_in=EquipmentIn, schema_out=EquipmentOut,
        prefix="/equipment", label="설비", entity="equipment", order_by=Equipment.name,
        validate=_step_ref,
    ),
    make_crud_router(
        model=ConditionDefinition, schema_in=ConditionDefinitionIn, schema_out=ConditionDefinitionOut,
        prefix="/condition-definitions", label="작업 조건 항목", entity="condition_definition",
        order_by=ConditionDefinition.name, validate=_step_ref,
    ),
    make_crud_router(
        model=Characteristic, schema_in=CharacteristicIn, schema_out=CharacteristicOut,
        prefix="/characteristics", label="측정 특성", entity="characteristic",
        order_by=Characteristic.name, validate=_char_ref,
    ),
    make_crud_router(
        model=DefectType, schema_in=DefectTypeIn, schema_out=DefectTypeOut,
        prefix="/defect-types", label="불량 유형", entity="defect_type", order_by=DefectType.code,
    ),
]


# ---- ProductDesign 은 하위 구성(components, steps)이 있어 별도 구현
design_router = APIRouter(prefix="/product-designs", tags=["설계 정보"])


def _design_snapshot(d: ProductDesign) -> dict:
    snap = audit.snapshot(d)
    snap["components"] = [audit.snapshot(c) for c in d.components]
    snap["steps"] = [audit.snapshot(s) for s in d.steps]
    return snap


def _apply_design(db: Session, d: ProductDesign, body: ProductDesignIn) -> None:
    require_exists(db, ItemType, body.output_item_type_id, "출력 부품 종류")
    for c in body.components:
        require_exists(db, ItemType, c.item_type_id, "구성 부품 종류")
    for s in body.steps:
        require_exists(db, ProcessStep, s.process_step_id, "공정 단계")
    d.name = body.name
    d.output_item_type_id = body.output_item_type_id
    d.version = body.version
    d.description = body.description
    d.is_active = body.is_active
    d.components = [ProductDesignComponent(**c.model_dump()) for c in body.components]
    d.steps = [ProductDesignStep(**s.model_dump()) for s in body.steps]


@design_router.get("", response_model=list[ProductDesignOut])
def list_designs(db: Session = Depends(get_db), _=Depends(viewer)):
    return db.scalars(select(ProductDesign).order_by(ProductDesign.name, ProductDesign.version)).all()


@design_router.get("/{design_id}", response_model=ProductDesignOut)
def get_design(design_id: int, db: Session = Depends(get_db), _=Depends(viewer)):
    d = db.get(ProductDesign, design_id)
    if d is None:
        raise not_found("설계 정보")
    return d


@design_router.post("", response_model=ProductDesignOut, status_code=201)
def create_design(body: ProductDesignIn, db: Session = Depends(get_db), user: User = Depends(admin)):
    d = ProductDesign()
    _apply_design(db, d, body)
    db.add(d)
    db.flush()
    audit.record(db, user, "create", "product_design", d.id, after=_design_snapshot(d))
    db.commit()
    return d


@design_router.put("/{design_id}", response_model=ProductDesignOut)
def update_design(
    design_id: int, body: ProductDesignIn, db: Session = Depends(get_db), user: User = Depends(admin)
):
    d = db.get(ProductDesign, design_id)
    if d is None:
        raise not_found("설계 정보")
    before = _design_snapshot(d)
    _apply_design(db, d, body)
    db.flush()
    audit.record(db, user, "update", "product_design", d.id, before=before, after=_design_snapshot(d))
    db.commit()
    return d


@design_router.delete("/{design_id}", status_code=204)
def delete_design(design_id: int, db: Session = Depends(get_db), user: User = Depends(admin)):
    d = db.get(ProductDesign, design_id)
    if d is None:
        raise not_found("설계 정보")
    before = _design_snapshot(d)
    db.delete(d)
    audit.record(db, user, "delete", "product_design", design_id, before=before)
    db.commit()


routers.append(design_router)
