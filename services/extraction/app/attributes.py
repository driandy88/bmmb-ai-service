from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_admin_key
from app.config import (
    AttributeInUseError,
    AttributeNotFoundError,
    NameAlreadyExistsError,
    create_attribute,
    delete_attribute,
    get_attribute,
    list_attributes,
    update_attribute,
)
from app.schemas import AttributeCreate, AttributeOut, AttributeUpdate

router = APIRouter(prefix="/attributes", tags=["Attributes"])


@router.get("/", response_model=list[AttributeOut])
def get_attributes():
    return list_attributes()


@router.get("/{attribute_id}", response_model=AttributeOut)
def get_attribute_detail(attribute_id: str):
    try:
        return get_attribute(attribute_id)
    except AttributeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/", response_model=AttributeOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin_key)])
def post_attribute(payload: AttributeCreate):
    try:
        return create_attribute(payload.name, payload.description, payload.data_type, payload.example)
    except NameAlreadyExistsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{attribute_id}", response_model=AttributeOut, dependencies=[Depends(require_admin_key)])
def put_attribute(attribute_id: str, payload: AttributeUpdate):
    try:
        return update_attribute(attribute_id, payload.model_dump(exclude_unset=True))
    except AttributeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NameAlreadyExistsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{attribute_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin_key)])
def delete_attribute_route(attribute_id: str):
    try:
        delete_attribute(attribute_id)
    except AttributeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AttributeInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
