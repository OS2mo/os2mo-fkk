# Generated by ariadne-codegen
# Source: queries.graphql

from uuid import UUID

from .base_model import BaseModel


class UpdateClass(BaseModel):
    class_update: "UpdateClassClassUpdate"


class UpdateClassClassUpdate(BaseModel):
    uuid: UUID


UpdateClass.model_rebuild()
