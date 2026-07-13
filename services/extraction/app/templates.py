from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_admin_key
from app.config import (
    AttributeNotFoundError,
    NameAlreadyExistsError,
    TemplateNotFoundError,
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)
from app.schema_builder import render_extraction_prompt
from app.schemas import TemplateCreate, TemplateOut, TemplateUpdate

router = APIRouter(prefix="/templates", tags=["Templates"])


@router.get("/", response_model=list[TemplateOut])
def get_templates():
    return list_templates()


@router.get("/{template_id}", response_model=TemplateOut)
def get_template_detail(template_id: str):
    try:
        return get_template(template_id)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/", response_model=TemplateOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin_key)])
def post_template(payload: TemplateCreate):
    try:
        tmpl = create_template(
            payload.name,
            payload.description,
            payload.group_name,
            payload.llm_prompt,
            [a.model_dump() for a in payload.attributes],
        )
    except NameAlreadyExistsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AttributeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if not payload.llm_prompt:
        # Same fallback as universal_data_extractor: auto-generate the prompt
        # from the attributes just wired in, if the caller didn't supply one.
        tmpl = update_template(tmpl["id"], {"llm_prompt": render_extraction_prompt(tmpl)}, None)
    return tmpl


@router.put("/{template_id}", response_model=TemplateOut, dependencies=[Depends(require_admin_key)])
def put_template(template_id: str, payload: TemplateUpdate):
    fields = payload.model_dump(exclude_unset=True, exclude={"attributes"})
    attributes = None if payload.attributes is None else [a.model_dump() for a in payload.attributes]
    try:
        return update_template(template_id, fields, attributes)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NameAlreadyExistsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AttributeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin_key)])
def delete_template_route(template_id: str):
    try:
        delete_template(template_id)
    except TemplateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
