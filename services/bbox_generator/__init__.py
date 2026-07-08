"""
bbox-generator service — locates LLM-extracted values inside their source
document (PDF text layer or OCR for images) and returns normalized bounding
boxes, without ever asking the LLM for coordinates.

    from services.bbox_generator.bbox_aligner import align_fields
    result = align_fields(extracted, field_types, doc_path)  # -> {field: {value, bbox, match_quality}}

As a FastAPI router, to mount into another service's app (see api.py):
    from services.bbox_generator.api import router
    app.include_router(router)
(Not imported into this package's top-level namespace, so importing
services.bbox_generator doesn't require fastapi to be installed unless you
actually use the router.)
"""
