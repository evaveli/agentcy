#src/agentcy/pydantic_models/single_step_request_model.py
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import json

class Scope(BaseModel):
    identifier: str
    label: str

class Option(BaseModel):
    name: str
    description: str
    isActive: bool
    isDefault: bool
    scope: Scope

class Guide(BaseModel):
    name: str
    description: str
    type: str
    isActive: bool
    scope: str
    options: List[Option]

class Assistant(BaseModel):
    name: str
    type: str
    guides: List[Guide]

class Content(BaseModel):
    type: str
    data: str

class Input(BaseModel):
    assistants: List[Assistant]
    content: List[Content]
    context: str









class StepRequest(BaseModel):
    step_id: str
    article_id: str
    options: Optional[List[Option]] = None


class Scope22(BaseModel):
    identifier: str
    label: str


class Content2(BaseModel):
    name: str
    description: str
    isActive: bool
    isDefault: bool
    scope: Optional[Scope22] = None


class GuideBaseModel2(BaseModel):
    name: str
    description: str
    type: str
    isActive: bool
    scope: str


class GuideExtraModel2(GuideBaseModel2):
    options: List[Content2] | str

    # @validator('options')
    # def check_empty_list(cls, v):
    #     if isinstance(v, str):
    #         return v
    #     if not v:
    #         raise ValueError('List must not be empty')
    #     for item in v:
    #         if not isinstance(item, Content):
    #             raise ValueError('Each item in the list must be of type Content')
    #     return v


class GuidesAssistantsModel2(BaseModel):
    name: str
    type: str
    guides: List[GuideExtraModel2]


class GuideModel2(BaseModel):
    assistants: list[GuidesAssistantsModel2]



class StepRequest22(BaseModel):
    step_id: str
    article_id: str
    input_source: str
    assistants:Optional[list[GuidesAssistantsModel2]] = None


scope_example = Scope(identifier="scope1", label="Scope 1")

option_example = Option(
    name="Option 1",
    description="Description for option 1",
    isActive=True,
    isDefault=False,
    scope=scope_example
)

guide_example = Guide(
    name="Guide 1",
    description="Description for guide 1",
    type="Type A",
    isActive=True,
    scope="Scope 1",
    options=[option_example]
)

assistant_example = Assistant(
    name="Assistant 1",
    type="Type A",
    guides=[guide_example]
)

content_example = Content(
    type="ContentType",
    data="ContentData"
)

input_example = Input(
    assistants=[assistant_example],
    content=[content_example],
    context="Example context"
)

step_request_example = StepRequest(
    step_id="1",
    article_id="fsfsdfsdf",
)


json_data = step_request_example.model_dump_json()
print(json_data)